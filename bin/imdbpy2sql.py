#!/usr/bin/env python
# -*- coding: UTF-8 -*-
"""
imdbpy2sql.py script.

This script puts the data of the plain text data files into a
SQL database.

Copyright 2005-2006 Davide Alberani <da@erlug.linux.it>
               2006 Giuseppe "Cowo" Corbelli <cowo --> lugbs.linux.it>

This program is free software; you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation; either version 2 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program; if not, write to the Free Software
Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  USA
"""


import os, sys, getopt, time, re
from gzip import GzipFile
from types import UnicodeType

from sqlobject import *

from imdb.parser.sql import soundex
from imdb.parser.sql.dbschema import *
from imdb.utils import analyze_title, analyze_name, \
        build_name, build_title, normalizeName, _articles
from imdb.parser.local.movieParser import _bus, _ldk, _lit, _links_sect
from imdb.parser.local.personParser import _parseBiography
from imdb._exceptions import IMDbParserError

re_nameImdbIndex = re.compile(r'\(([IVXLCDM]+)\)')

HELP = """imdbpy2sql usage:
    %s -d /directory/with/PlainTextDataFiles/ -u URI

        # NOTE: URI is something along the line:
                scheme://[user[:password]@]host[:port]/database[?parameters]

                Examples:
                mysql://user:password@host/database
                postgres://user@host/database
                sqlite:/tmp/imdb.db
                sqlite:/C|/full/path/to/database

                See README.sqldb for more information.
""" % sys.argv[0]

# Directory containing the IMDb's Plain Text Data Files.
IMDB_PTDF_DIR = None
# URI used to connect to the database.
URI = None

# Manage arguments list.
try:
    optlist, args = getopt.getopt(sys.argv[1:], 'u:d:h',
                                                ['uri=', 'data=', 'help'])
except getopt.error, e:
    print 'Troubles with arguments.'
    print HELP
    sys.exit(2)

for opt in optlist:
    if opt[0] in ('-d', '--data'):
        IMDB_PTDF_DIR = opt[1]
    elif opt[0] in ('-u', '--uri'):
        URI = opt[1]
    elif opt[0] in ('-h', '--help'):
        print HELP
        sys.exit(0)

if IMDB_PTDF_DIR is None:
    print 'You must supply the directory with the plain text data files'
    print HELP
    sys.exit(2)

if URI is None:
    print 'You must supply the URI for the database connection'
    print HELP
    sys.exit(2)

# Connect to the database.
conn = setConnection(URI)
# Extract exceptions to trap.
OperationalError = conn.module.OperationalError
IntegrityError = conn.module.IntegrityError

connectObject = conn.getConnection()
# Cursor object.
CURS = connectObject.cursor()

# Name of the database and style of the parameters.
DB_NAME = conn.dbName
PARAM_STYLE = conn.module.paramstyle


# Truncate the current database.
print 'DROPPING current database...',
sys.stdout.flush()
dropTables()
print 'done!'
print 'CREATING new tables...',
sys.stdout.flush()
createTables()
print 'done!'


def tableName(table):
    """Return a string with the name of the table in the current db."""
    return table.sqlmeta.table

def colName(table, column):
    """Return a string with the name of the column in the current db."""
    if column == 'id':
        return table.sqlmeta.idName
    return table.sqlmeta.columns[column].dbName


class RawValue(object):
    """String-like objects to store raw SQL parameters, that are not
    intended to be replaced with positional parameters, in the query."""
    def __init__(self, s, v):
        self.string = s
        self.value = v
    def __str__(self):
        return self.string


def _makeConvNamed(cols):
    """Return a function to be used to convert a list of parameters
    from positional style to named style (convert from a list of
    tuples to a list of dictionaries."""
    nrCols = len(cols)
    def _converter(params):
        for paramIndex, paramSet in enumerate(params):
            d = {}
            for i in xrange(nrCols):
                d[cols[i]] = paramSet[i]
            params[paramIndex] = d
        return params
    return _converter

def createSQLstr(table, cols, command='INSERT'):
    """Given a table and a list of columns returns a sql statement
    useful to insert a set of data in the database.
    Along with the string, also a function useful to convert parameters
    from positional to named style is returned."""
    sqlstr = '%s INTO %s ' % (command, tableName(table))
    colNames = []
    values = []
    convCols = []
    count = 1
    def _valStr(s, index):
        if DB_NAME in ('mysql', 'postgres'): return '%s'
        elif PARAM_STYLE == 'format': return '%s'
        elif PARAM_STYLE == 'qmark': return '?'
        elif PARAM_STYLE == 'numeric': return ':%s' % index
        elif PARAM_STYLE == 'named': return ':%s' % s
        elif PARAM_STYLE == 'pyformat': return '%(' + s + ')s'
    for col in cols:
        if isinstance(col, RawValue):
            colNames.append(colName(table, col.string))
            values.append(str(col.value))
        elif col == 'id':
            colNames.append(table.sqlmeta.idName)
            values.append(_valStr('id', count))
            convCols.append(col)
            count += 1
        else:
            colNames.append(colName(table, col))
            values.append(_valStr(col, count))
            convCols.append(col)
            count += 1
    sqlstr += '(%s) ' % ', '.join(colNames)
    sqlstr += 'VALUES (%s)' % ', '.join(values)
    if DB_NAME not in ('mysql', 'postgres') and \
            PARAM_STYLE in ('named', 'pyformat'):
        converter = _makeConvNamed(convCols)
    else:
        # Return the list itself.
        converter = lambda x: x
    return sqlstr, converter

def _(s):
    """Nicely print a string to sys.stdout."""
    if not isinstance(s, UnicodeType):
        s = unicode(s, 'utf_8')
    s = s.encode(sys.stdout.encoding or 'utf_8', 'replace')
    return s

# Show time consumed by the single function call.
CTIME = int(time.time())
BEGIN_TIME = CTIME
def t(s):
    global CTIME
    nt = int(time.time())
    print '# TIME', s, ': %d min, %s sec.' % divmod(nt-CTIME, 60)
    CTIME = nt

def title_soundex(title):
    """Return the soundex code for the given title; the (optional) ending
    article is pruned.  It assumes to receive a title without year/imdbIndex
    or kind indications, but just the title string, as the one in the
    analyze_title(title)['title'] value."""
    title = unicode(title, 'utf_8')
    # Prune non-ascii chars from the string.
    title = title.encode('ascii', 'replace')
    if not title: return None
    ts = title.split(', ')
    # Strip the ending article, if any.
    if ts[-1].lower() in _articles:
        title = ', '.join(ts[:-1])
    return soundex(title)

def name_soundexes(name):
    """Return three soundex codes for the given name; the name is assumed
    to be in the 'surname, name' format, without the imdbIndex indication,
    as the one in the analyze_name(name)['name'] value.
    The first one is the soundex of the name in the canonical format.
    The second is the soundex of the name in the normal format, if different
    from the first one.
    The third is the soundex of the surname, if different from the
    other two values."""
    name = unicode(name, 'utf_8')
    # Prune non-ascii chars from the string.
    name = name.encode('ascii', 'ignore')
    if not name: return (None, None, None)
    s1 = soundex(name)
    name_normal = normalizeName(name)
    s2 = soundex(name_normal)
    if s1 == s2: s2 = None
    namesplit = name.split(', ')
    s3 = soundex(namesplit[0])
    if s3 and s3 in (s1, s2): s3 = None
    return (s1, s2, s3)


# Handle laserdisc keys.
for key, value in _ldk.items():
    _ldk[key] = 'LD %s' % value


# Tags to identify where the meaningful data begin/end in files.
MOVIES = 'movies.list.gz'
MOVIES_START = ('MOVIES LIST', '===========', '')
MOVIES_STOP = '--------------------------------------------------'
CAST_START = ('Name', '----')
CAST_STOP = '-----------------------------'
RAT_START = ('MOVIE RATINGS REPORT', '',
            'New  Distribution  Votes  Rank  Title')
RAT_STOP = '\n'
RAT_TOP250_START = ('note: for this top 250', '', 'New  Distribution')
RAT_BOT10_START = ('BOTTOM 10 MOVIES', '', 'New  Distribution')
TOPBOT_STOP = '\n'
AKAT_START = ('AKA TITLES LIST', '=============', '', '', '')
AKAT_IT_START = ('AKA TITLES LIST ITALIAN', '=======================', '', '')
AKAT_DE_START = ('AKA TITLES LIST GERMAN', '======================', '')
AKAT_ISO_START = ('AKA TITLES LIST ISO', '===================', '')
AKAT_HU_START = ('AKA TITLES LIST HUNGARIAN', '=========================', '')
AKAT_NO_START = ('AKA TITLES LIST NORWEGIAN', '=========================', '')
AKAN_START = ('AKA NAMES LIST', '=============', '')
AV_START = ('ALTERNATE VERSIONS LIST', '=======================', '', '')
MINHASH_STOP = '-------------------------'
GOOFS_START = ('GOOFS LIST', '==========', '')
QUOTES_START = ('QUOTES LIST', '=============')
CC_START = ('CRAZY CREDITS', '=============')
BIO_START = ('BIOGRAPHY LIST', '==============')
BUS_START = ('BUSINESS LIST', '=============', '')
BUS_STOP = '                                    ====='
CER_START = ('CERTIFICATES LIST', '=================')
COL_START = ('COLOR INFO LIST', '===============')
COU_START = ('COUNTRIES LIST', '==============')
DIS_START = ('DISTRIBUTORS LIST', '=================', '')
GEN_START = ('8: THE GENRES LIST', '==================', '')
KEY_START = ('8: THE KEYWORDS LIST', '====================', '')
LAN_START = ('LANGUAGE LIST', '=============')
LOC_START = ('LOCATIONS LIST', '==============', '')
MIS_START = ('MISCELLANEOUS COMPANY LIST', '==========================')
PRO_START = ('PRODUCTION COMPANIES LIST', '=========================', '')
RUN_START = ('RUNNING TIMES LIST', '==================')
SOU_START = ('SOUND-MIX LIST', '==============')
SFX_START = ('SFXCO COMPANIES LIST', '====================', '')
TCN_START = ('TECHNICAL LIST', '==============', '', '')
LSD_START = ('LASERDISC LIST', '==============', '------------------------')
LIT_START = ('LITERATURE LIST', '===============', '')
LIT_STOP = 'COPYING POLICY'
LINK_START = ('MOVIE LINKS LIST', '================', '')
MPAA_START = ('MPAA RATINGS REASONS LIST', '=========================')
PLOT_START = ('PLOT SUMMARIES LIST', '===================', '')
RELDATE_START = ('RELEASE DATES LIST', '==================')
SNDT_START = ('SOUNDTRACKS LIST', '================', '', '', '')
TAGL_START = ('TAG LINES LIST', '==============', '', '')
TAGL_STOP = '-----------------------------------------'
TRIV_START = ('FILM TRIVIA', '===========', '')
COMPCAST_START = ('CAST COVERAGE TRACKING LIST', '===========================')
COMPCREW_START = ('CREW COVERAGE TRACKING LIST', '===========================')
COMP_STOP = '---------------'

GzipFileRL = GzipFile.readline
class SourceFile(GzipFile):
    """Instances of this class are used to read gzipped files,
    starting from a defined line to a (optionally) given end."""
    def __init__(self, filename=None, mode=None, start=(), stop=None,
                    pwarning=1, *args, **kwds):
        filename = os.path.join(IMDB_PTDF_DIR, filename)
        try:
            GzipFile.__init__(self, filename, mode, *args, **kwds)
        except IOError, e:
            if not pwarning: raise
            print 'WARNING WARNING WARNING'
            print 'WARNING unable to read the "%s" file.' % filename
            print 'WARNING The file will be skipped, and the contained'
            print 'WARNING information will NOT be stored in the database.'
            print 'WARNING Complete error: ', e
            # re-raise the exception.
            raise
        self.start = start
        for item in start:
            itemlen = len(item)
            for line in self:
                if line[:itemlen] == item: break
        self.set_stop(stop)

    def set_stop(self, stop):
        if stop is not None:
            self.stop = stop
            self.stoplen = len(self.stop)
            self.readline = self.readline_checkEnd
        else:
            self.readline = self.readline_NOcheckEnd

    def readline_NOcheckEnd(self, size=-1):
        line = GzipFile.readline(self, size)
        return unicode(line, 'latin_1').encode('utf_8')

    def readline_checkEnd(self, size=-1):
        line = GzipFile.readline(self, size)
        if self.stop is not None and line[:self.stoplen] == self.stop: return ''
        return unicode(line, 'latin_1').encode('utf_8')

    def getByHashSections(self):
        return getSectionHash(self)

    def getByNMMVSections(self):
        return getSectionNMMV(self)


def getSectionHash(fp):
    """Return sections separated by lines starting with #"""
    curSectList = []
    curSectListApp = curSectList.append
    curTitle = ''
    joiner = ''.join
    for line in fp:
        if line and line[0] == '#':
            if curSectList and curTitle:
                yield curTitle, joiner(curSectList)
                curSectList[:] = []
                curTitle = ''
            curTitle = line[2:]
        else: curSectListApp(line)
    if curSectList and curTitle:
        yield curTitle, joiner(curSectList)
        curSectList[:] = []
        curTitle = ''

NMMVSections = dict([(x, None) for x in ('MV: ', 'NM: ', 'OT: ', 'MOVI')])
NMMVSectionsHASK = NMMVSections.has_key
def getSectionNMMV(fp):
    """Return sections separated by lines starting with 'NM: ', 'MV: ',
    'OT: ' or 'MOVI'."""
    curSectList = []
    curSectListApp = curSectList.append
    curNMMV = ''
    joiner = ''.join
    for line in fp:
        if NMMVSectionsHASK(line[:4]):
            if curSectList and curNMMV:
                yield curNMMV, joiner(curSectList)
                curSectList[:] = []
                curNMMV = ''
            if line[:4] == 'MOVI': curNMMV = line[6:]
            else: curNMMV = line[4:]
        elif not (line and line[0] == '-'): curSectListApp(line)
    if curSectList and curNMMV:
        yield curNMMV, joiner(curSectList)
        curSectList[:] = []
        curNMMV = ''


class _BaseCache(dict):
    """Base class for Movie and Person basic information."""
    def __init__(self, d=None, flushEvery=18000, counterInit=1):
        dict.__init__(self)
        self.set_counter_init(counterInit)
        # Flush data into the SQL database every flushEvery entries.
        self.flushEvery = flushEvery
        self._tmpDict = {}
        self._flushing = 0
        self._deferredData = {}
        self._recursionLevel = 1
        if d is not None:
            for k, v in d.iteritems(): self[k] = v

    def set_counter_init(self, counterInit):
        self.counterInit = self.counter = counterInit

    def __setitem__(self, key, value):
        """Every time a key is set, its value is discarded and substituted
        with counter; every flushEvery, the temporary dictionary is
        flushed to the database, and then zeroed."""
        counter = self.counter
        if counter % self.flushEvery == 0:
            self.flush()
        dict.__setitem__(self, key, counter)
        if not self._flushing:
            self._tmpDict[key] = counter
        else:
            self._deferredData[key] = self.counter + 1

    def flush(self, quiet=0, _resetRecursion=1):
        """Flush to the database."""
        if self._flushing: return
        self._flushing = 1
        if _resetRecursion: self._recursionLevel = 1
        if self._recursionLevel >= 5:
            print 'WARNING recursion level exceded trying to flush data'
            print 'WARNING this batch of data is lost.'
            self._tmpDict.clear()
            return
        if self._tmpDict:
            try:
                self._toDB(quiet)
                self._tmpDict.clear()
            except OperationalError, e:
                # Dataset too large; split it in two and retry.
                print ' * TOO MANY DATA (%s items), SPLITTING...' % \
                        len(self._tmpDict)
                self._recursionLevel += 1
                c1 = self.__class__()
                c2 = self.__class__()
                newflushEvery = self.flushEvery / 2
                c1.flushEvery = newflushEvery
                c2.flushEvery = newflushEvery
                poptmpd = self._tmpDict.popitem
                for x in xrange(len(self._tmpDict)/2):
                    k, v = poptmpd()
                    c1._tmpDict[k] = v
                c2._tmpDict = self._tmpDict
                c1.flush(quiet=quiet, _resetRecursion=0)
                c2.flush(quiet=quiet, _resetRecursion=0)
                self._tmpDict.clear()
        self._flushing = 0
        # Flush also deferred data.
        if self._deferredData:
            self._tmpDict = self._deferredData
            self.flush()
            self._deferredData = {}

    def populate(self):
        """Populate the dictionary from the database."""
        raise NotImplementedError

    def _toDB(self, quiet=0):
        """Write the dictionary to the database."""
        raise NotImplementedError

    def add(self, key, miscData=None):
        """Insert a new key and return its value."""
        c = self.counter
        # miscData=[('a_dict', 'value')] will set self.a_dict's c key
        # to 'value'.
        if miscData is not None:
            for d_name, data in miscData:
                getattr(self, d_name)[c] = data
        self[key] = None
        self.counter += 1
        return c

    def addUnique(self, key, miscData=None):
        """Insert a new key and return its value; if the key is already
        in the dictionary, its previous  value is returned."""
        if self.has_key(key): return self[key]
        else: return self.add(key, miscData)


def fetchsome(curs, size=18000):
    """Yes, I've read the Python Cookbook! :-)"""
    while 1:
        res = CURS.fetchmany(size)
        if not res: break
        for r in res: yield r

class MoviesCache(_BaseCache):
    """Manage the movies list."""
    className = 'MoviesCache'

    def __init__(self, *args, **kwds):
        _BaseCache.__init__(self, *args, **kwds)
        self.episodesYear = {}
        self.sqlstr, self.converter = createSQLstr(Title, ('id', 'title',
                                    'imdbIndex', 'kindID', 'productionYear',
                                    'phoneticCode', 'episodeOfID',
                                    'seasonNr', 'episodeNr'))

    def populate(self):
        print ' * POPULATING %s...' % self.className
        titleTbl = tableName(Title)
        movieidCol = colName(Title, 'id')
        titleCol = colName(Title, 'title')
        kindidCol = colName(Title, 'kindID')
        yearCol = colName(Title, 'productionYear')
        imdbindexCol = colName(Title, 'imdbIndex')
        episodeofidCol = colName(Title, 'episodeOfID')
        seasonNrCol = colName(Title, 'seasonNr')
        episodeNrCol = colName(Title, 'episodeNr')
        sqlPop = 'SELECT %s, %s, %s, %s, %s, %s, %s, %s FROM %s;' % (movieidCol,
                    titleCol, kindidCol, yearCol, imdbindexCol,
                    episodeofidCol, seasonNrCol, episodeNrCol, titleTbl)
        CURS.execute(sqlPop)
        _oldcacheValues = Title.sqlmeta.cacheValues
        Title.sqlmeta.cacheValues = False
        for x in fetchsome(CURS, self.flushEvery):
            mdict = {'title': unicode(x[1], 'utf_8'), 'kind': KIND_STRS[x[2]],
                    'year': x[3], 'imdbIndex': x[4]}
            if mdict['imdbIndex'] is None: del mdict['imdbIndex']
            if mdict['year'] is None: del mdict['year']
            else: mdict['year'] = str(mdict['year'])
            episodeOfID = x[5]
            if episodeOfID is not None:
                s = Title.get(episodeOfID)
                series_d = {'title': s.title,
                            'kind': str(s.kind.kind),
                            'year': s.productionYear, 'imdbIndex': s.imdbIndex}
                if series_d['imdbIndex'] is None: del series_d['imdbIndex']
                if series_d['year'] is None: del series_d['year']
                else: series_d['year'] = str(series_d['year'])
                mdict['episode of'] = series_d
            title = build_title(mdict, canonical=1, ptdf=1)
            dict.__setitem__(self, title, x[0])
        self.counter = Title.select().count() + 1
        Title.sqlmeta.cacheValues = _oldcacheValues

    def _toDB(self, quiet=0):
        if not quiet:
            print ' * FLUSHING %s...' % self.className
            sys.stdout.flush()
        l = []
        lapp = l.append
        tmpDictiter = self._tmpDict.iteritems
        for k, v in tmpDictiter():
            try:
                t = analyze_title(k)
            except IMDbParserError:
                if k and k.strip():
                    print 'WARNING %s._toDB() invalid title:' % self.className,
                    print _(k)
                continue
            tget = t.get
            episodeOf = None
            kind = tget('kind')
            if kind == 'episode':
                #series title
                stitle = build_title(tget('episode of'), canonical=1)
                episodeOf = self.addUnique(stitle)
                del t['episode of']
                year = self.episodesYear.get(v)
                if year is not None:
                    try: t['year'] = int(year)
                    except ValueError: pass
            title = tget('title')
            soundex = title_soundex(title)
            lapp((v, title, tget('imdbIndex'), KIND_IDS[kind],
                    tget('year'), soundex, episodeOf,
                    tget('season'), tget('episode')))
        self._runCommand(l)

    def _runCommand(self, dataList):
        CURS.executemany(self.sqlstr, self.converter(dataList))


class PersonsCache(_BaseCache):
    """Manage the persons list."""

    def __init__(self, *args, **kwds):
        _BaseCache.__init__(self, *args, **kwds)
        self.sqlstr, self.converter = createSQLstr(Name, ['id', 'name',
                                'imdbIndex', 'namePcodeCf', 'namePcodeNf',
                                'surnamePcode'])

    def populate(self):
        print ' * POPULATING PersonsCache...'
        nameTbl = tableName(Name)
        personidCol = colName(Name, 'id')
        nameCol = colName(Name, 'name')
        imdbindexCol = colName(Name, 'imdbIndex')
        CURS.execute('SELECT %s, %s, %s FROM %s;' % (personidCol, nameCol,
                                                    imdbindexCol, nameTbl))
        _oldcacheValues = Name.sqlmeta.cacheValues
        Name.sqlmeta.cacheValues = False
        for x in fetchsome(CURS, self.flushEvery):
            nd = {'name': unicode(x[1], 'utf_8')}
            if x[2]: nd['imdbIndex'] = x[2]
            name = build_name(nd, canonical=1)
            dict.__setitem__(self, name, x[0])
        self.counter = Name.select().count() + 1
        Name.sqlmeta.cacheValues = _oldcacheValues

    def _toDB(self, quiet=0):
        if not quiet:
            print ' * FLUSHING PersonsCache...'
            sys.stdout.flush()
        l = []
        lapp = l.append
        tmpDictiter = self._tmpDict.iteritems
        for k, v in tmpDictiter():
            try:
                t = analyze_name(k)
            except IMDbParserError:
                if k and k.strip():
                    print 'WARNING PersonsCache._toDB() invalid name:', _(k)
                continue
            tget = t.get
            name = tget('name')
            namePcodeCf, namePcodeNf, surnamePcode = name_soundexes(name)
            lapp((v, name, tget('imdbIndex'),
                namePcodeCf, namePcodeNf, surnamePcode))
        CURS.executemany(self.sqlstr, self.converter(l))


class SQLData(dict):
    """Variable set of information, to be stored from time to time
    to the SQL database."""
    def __init__(self, table=None, cols=None, sqlString='', converter=None,
                d={}, flushEvery=20000, counterInit=1):
        if not sqlString:
            if not (table and cols):
                raise TypeError, '"table" or "cols" unspecified'
            sqlString, converter = createSQLstr(table, cols)
        elif converter is None:
            raise TypeError, '"sqlString" or "converter" unspecified'
        dict.__init__(self)
        self.counterInit = counterInit
        self.counter = counterInit
        self.flushEvery = flushEvery
        self.sqlString = sqlString
        self.converter = converter
        self._recursionLevel = 1
        for k, v in d.items(): self[k] = v

    def __setitem__(self, key, value):
        """The value is discarded, the counter is used as the 'real' key
        and the user's 'key' is used as its values."""
        counter = self.counter
        if counter % self.flushEvery == 0:
            self.flush()
        dict.__setitem__(self, counter, key)
        self.counter += 1

    def add(self, key):
        self[key] = None

    def flush(self, _resetRecursion=1):
        if not self: return
        # XXX: it's safer to flush MoviesCache and PersonsCache, to preserve
        #      consistency of ForeignKey, but it can also slow down everything
        #      a bit...
        CACHE_MID.flush(quiet=1)
        CACHE_PID.flush(quiet=1)
        if _resetRecursion: self._recursionLevel = 1
        if self._recursionLevel >= 5:
            print 'WARNING recursion level exceded trying to flush data'
            print 'WARNING this batch of data is lost.'
            self.clear()
            self.counter = self.counterInit
            return
        try:
            self._toDB()
            self.clear()
            self.counter = self.counterInit
        except OperationalError, e:
            print ' * TOO MANY DATA (%s items), SPLITTING...' % len(self)
            self._recursionLevel += 1
            newdata = self.__class__()
            newflushEvery = self.flushEvery / 2
            self.flushEvery = newflushEvery
            newdata.flushEvery = newflushEvery
            newdata.sqlString = self.sqlString
            popitem = self.popitem
            dsi = dict.__setitem__
            for x in xrange(len(self)/2):
                k, v = popitem()
                dsi(newdata, k, v)
            newdata.flush(_resetRecursion=0)
            self.flush(_resetRecursion=0)
            self.clear()
            self.counter = self.counterInit

    def _toDB(self):
        print ' * FLUSHING SQLData...'
        CURS.executemany(self.sqlString, self.converter(self.values()))


# Miscellaneous functions.

def unpack(line, headers, sep='\t'):
    """Given a line, split at seps and return a dictionary with key
    from the header list.
    E.g.:
        line = '      0000000124    8805   8.4  Incredibles, The (2004)'
        header = ('votes distribution', 'votes', 'rating', 'title')
        seps=('  ',)

    will returns: {'votes distribution': '0000000124', 'votes': '8805',
                    'rating': '8.4', 'title': 'Incredibles, The (2004)'}
    """
    r = {}
    ls1 = filter(None, line.split(sep))
    for index, item in enumerate(ls1):
        try: name = headers[index]
        except IndexError: name = 'item%s' % index
        r[name] = item.strip()
    return r

def _parseMinusList(fdata):
    """Parse a list of lines starting with '- '."""
    rlist = []
    tmplist = []
    for line in fdata:
        if line and line[:2] == '- ':
            if tmplist: rlist.append(' '.join(tmplist))
            l = line[2:].strip()
            if l: tmplist[:] = [l]
            else: tmplist[:] = []
        else:
            l = line.strip()
            if l: tmplist.append(l)
    if tmplist: rlist.append(' '.join(tmplist))
    return rlist


def _parseColonList(lines, replaceKeys):
    """Parser for lists with "TAG: value" strings."""
    out = {}
    for line in lines:
        line = line.strip()
        if not line: continue
        cols = line.split(':', 1)
        if len(cols) < 2: continue
        k = cols[0]
        k = replaceKeys.get(k, k)
        v = ' '.join(cols[1:]).strip()
        if not out.has_key(k): out[k] = []
        out[k].append(v)
    return out


# Functions used to manage data files.

def readMovieList():
    """Read the movies.list.gz file."""
    try: mdbf = SourceFile(MOVIES, start=MOVIES_START, stop=MOVIES_STOP)
    except IOError: return
    count = 0
    for line in mdbf:
        line_d = unpack(line, ('title', 'year'))
        title = line_d['title']
        yearData = None
        # Collect 'year' column for tv series' episodes.
        if title[-1:] == '}':
            yearData = [('episodesYear', line_d['year'])]
        mid = CACHE_MID.addUnique(title, yearData)
        if count % 10000 == 0:
            print 'SCANNING movies:', _(title),
            print '(movieID: %s)' % mid
        count += 1
    CACHE_MID.flush()
    CACHE_MID.episodesYear.clear()
    mdbf.close()


def doCast(fp, roleid, rolename):
    """Populate the cast table."""
    pid = None
    count = 0
    name = ''
    roleidVal = RawValue('roleID', roleid)
    sqldata = SQLData(table=CastInfo, cols=['personID', 'movieID',
                        'personRole', 'note', 'nrOrder', roleidVal])
    if rolename == 'miscellaneous crew': sqldata.flushEvery = 10000
    for line in fp:
        if line and line[0] != '\t':
            if line[0] == '\n': continue
            sl = filter(None, line.split('\t'))
            if len(sl) != 2: continue
            name, line = sl
            pid = CACHE_PID.addUnique(name.strip())
        line = line.strip()
        ll = line.split('  ')
        title = ll[0]
        note = None
        role = None
        order = None
        for item in ll[1:]:
            if not item: continue
            if item[0] == '[':
                role = item[1:-1]
            elif item[0] == '(':
                note = item
            elif item[0] == '<':
                textor = item[1:-1]
                try:
                    order = long(textor)
                except ValueError:
                    os = textor.split(',')
                    if len(os) == 3:
                        try:
                            order = ((long(os[2])-1) * 1000) + \
                                    ((long(os[1])-1) * 100) + (long(os[0])-1)
                        except ValueError:
                            pass
        movieid = CACHE_MID.addUnique(title)
        sqldata.add((pid, movieid, role, note, order))
        if count % 10000 == 0:
            print 'SCANNING', rolename, ':',
            print _(name)
        count += 1
    sqldata.flush()
    print 'CLOSING %s...' % rolename


def castLists():
    """Read files listed in the 'role' column of the 'roletypes' table."""
    for rt in RoleType.select():
        roleid = rt.id
        rolename = fname = rt.role
        if rolename == 'guest':
            continue
        fname = fname.replace(' ', '-')
        if fname == 'actress': fname = 'actresses.list.gz'
        elif fname == 'miscellaneous-crew': fname = 'miscellaneous.list.gz'
        else: fname = fname + 's.list.gz'
        print 'DOING', fname
        try:
            f = SourceFile(fname, start=CAST_START, stop=CAST_STOP)
        except IOError:
            continue
        doCast(f, roleid, rolename)
        f.close()
        t('castLists(%s)' % rolename)


def doAkaNames():
    """People's akas."""
    pid = None
    count = 0
    try: fp = SourceFile('aka-names.list.gz', start=AKAN_START)
    except IOError: return
    sqldata = SQLData(table=AkaName, cols=['personID', 'name', 'imdbIndex',
                            'namePcodeCf', 'namePcodeNf', 'surnamePcode'])
    for line in fp:
        if line and line[0] != ' ':
            if line[0] == '\n': continue
            pid = CACHE_PID.addUnique(line.strip())
        else:
            line = line.strip()
            if line[:5] == '(aka ': line = line[5:]
            if line[-1:] == ')': line = line[:-1]
            try:
                name_dict = analyze_name(line)
            except IMDbParserError:
                if line: print 'WARNING: wrong name:', _(line)
                continue
            name = name_dict.get('name')
            namePcodeCf, namePcodeNf, surnamePcode = name_soundexes(name)
            sqldata.add((pid, name, name_dict.get('imdbIndex'),
                        namePcodeCf, namePcodeNf, surnamePcode))
            if count % 10000 == 0:
                print 'SCANNING akanames:', _(line)
            count += 1
    sqldata.flush()
    fp.close()


class AkasMoviesCache(MoviesCache):
    """A MoviesCache-like class used to populate the AkATitle table."""
    className = 'AkasMoviesCache'

    def __init__(self, *args, **kdws):
        MoviesCache.__init__(self, *args, **kdws)
        self.notes = {}
        self.ids = {}
        self.sqlstr, self.converter = createSQLstr(AkaTitle, ('id', 'movieID',
                            'title', 'imdbIndex', 'kindID', 'productionYear',
                            'phoneticCode', 'episodeOfID', 'seasonNr',
                            'episodeNr', 'note'))

    def _runCommand(self, dataList):
        new_dataList = []
        new_dataListapp = new_dataList.append
        while dataList:
            item = dataList.pop()
            # id used to store this entry.
            the_id = item[0]
            # id of the referred title.
            original_title_id = self.ids.get(the_id)
            new_item = [the_id, original_title_id]
            new_item += item[1:]
            new_item.append(self.notes.get(the_id))
            new_dataListapp(tuple(new_item))
        new_dataList.reverse()
        CURS.executemany(self.sqlstr, self.converter(new_dataList))
CACHE_MID_AKAS = AkasMoviesCache()


def doAkaTitles():
    """Movies' akas."""
    mid = None
    count = 0
    for fname, start in (('aka-titles.list.gz',AKAT_START),
                    ('italian-aka-titles.list.gz',AKAT_IT_START),
                    ('german-aka-titles.list.gz',AKAT_DE_START),
                    ('iso-aka-titles.list.gz',AKAT_ISO_START),
                    (os.path.join('contrib','hungarian-aka-titles.list.gz'),
                        AKAT_HU_START),
                    (os.path.join('contrib','norwegian-aka-titles.list.gz'),
                        AKAT_NO_START)):
        incontrib = 0
        pwarning = 1
        if start in (AKAT_HU_START, AKAT_NO_START):
            pwarning = 0
            incontrib = 1
        try:
            fp = SourceFile(fname, start=start,
                            stop='---------------------------',
                            pwarning=pwarning)
        except IOError:
            continue
        for line in fp:
            if line and line[0] != ' ':
                if line[0] == '\n': continue
                mid = CACHE_MID.addUnique(line.strip())
            else:
                res = unpack(line.strip(), ('title', 'note'))
                note = res.get('note')
                if incontrib:
                    if res.get('note'): note += ' '
                    else: note = ''
                    if start == AKAT_HU_START: note += '(Hungary)'
                    elif start == AKAT_NO_START: note += '(Norway)'
                akat = res.get('title', '')
                if akat[:5] == '(aka ': akat = akat[5:]
                if akat[-2:] in ('))', '})'): akat = akat[:-1]
                if count % 10000 == 0:
                    print 'SCANNING %s:' % fname[:-8].replace('-', ' '),
                    print _(akat)
                append_data = [('ids', mid)]
                if note is not None:
                    append_data.append(('notes', note))
                aka_id = CACHE_MID_AKAS.add(akat, append_data)
                count += 1
        fp.close()
    CACHE_MID_AKAS.flush()
    CACHE_MID_AKAS.clear()
    CACHE_MID_AKAS.notes.clear()
    CACHE_MID_AKAS.ids.clear()


def doMovieLinks():
    """Connections between movies."""
    mid = None
    count = 0
    sqldata = SQLData(table=MovieLink,
                cols=['movieID', 'linkedMovieID', 'linkTypeID'],
                flushEvery=10000)
    try: fp = SourceFile('movie-links.list.gz', start=LINK_START)
    except IOError: return
    for line in fp:
        if line and line[0] != ' ':
            if line[0] == '\n': continue
            title = line.strip()
            mid = CACHE_MID.addUnique(title)
            if count % 10000 == 0:
                print 'SCANNING movielinks:', _(title)
        else:
            line = line.strip()
            link_txt = unicode(line, 'utf_8').encode('ascii', 'replace')
            theid = None
            for k, lenkp1, v in MOVIELINK_IDS:
                if link_txt and link_txt[0] == '(' \
                        and link_txt[1:lenkp1+1] == k:
                    theid = v
                    break
            if theid is None: continue
            totitle = line[lenkp1+2:-1].strip()
            totitleid = CACHE_MID.addUnique(totitle)
            sqldata.add((mid, totitleid, theid))
        count += 1
    sqldata.flush()
    fp.close()


def minusHashFiles(fp, funct, defaultid, descr):
    """A file with lines starting with '# ' and '- '."""
    sqldata = SQLData(table=MovieInfo,
                        cols=['movieID', 'infoTypeID', 'info'])
    sqldata.flushEvery = 2500
    if descr == 'quotes': sqldata.flushEvery = 4000
    elif descr == 'soundtracks': sqldata.flushEvery = 3000
    elif descr == 'trivia': sqldata.flushEvery = 3000
    count = 0
    for title, text in fp.getByHashSections():
        title = title.strip()
        d = funct(text.split('\n'))
        mid = CACHE_MID.addUnique(title)
        if count % 5000 == 0:
            print 'SCANNING %s:' % descr,
            print _(title)
        for data in d:
            sqldata.add((mid, defaultid, data))
        count += 1
    sqldata.flush()


def doMinusHashFiles():
    """Files with lines starting with '# ' and '- '."""
    for fname, start in [('alternate versions',AV_START),
                         ('goofs',GOOFS_START), ('crazy credits',CC_START),
                         ('quotes',QUOTES_START),
                         ('soundtracks',SNDT_START),
                         ('trivia',TRIV_START)]:
        try:
            fp = SourceFile(fname.replace(' ', '-')+'.list.gz', start=start,
                        stop=MINHASH_STOP)
        except IOError:
            continue
        funct = _parseMinusList
        if fname == 'quotes': funct = getQuotes
        index = fname
        if index == 'soundtracks': index = 'soundtrack'
        minusHashFiles(fp, funct, INFO_TYPES[index], fname)
        fp.close()


def getTaglines():
    """Movie's taglines."""
    try: fp = SourceFile('taglines.list.gz', start=TAGL_START, stop=TAGL_STOP)
    except IOError: return
    sqldata = SQLData(table=MovieInfo,
                cols=['movieID', 'infoTypeID', 'info'],
                flushEvery=10000)
    count = 0
    for title, text in fp.getByHashSections():
        title = title.strip()
        mid = CACHE_MID.addUnique(title)
        for tag in text.split('\n'):
            tag = tag.strip()
            if not tag: continue
            if count % 10000 == 0:
                print 'SCANNING taglines:', _(title)
            sqldata.add((mid, INFO_TYPES['taglines'], tag))
        count += 1
    sqldata.flush()
    fp.close()


def getQuotes(lines):
    """Movie's quotes."""
    quotes = []
    qttl = []
    for line in lines:
        if line and line[:2] == '  ' and qttl and qttl[-1] and \
                not qttl[-1].endswith('::'):
            line = line.lstrip()
            if line: qttl[-1] += ' %s' % line
        elif not line.strip():
            if qttl: quotes.append('::'.join(qttl))
            qttl[:] = []
        else:
            line = line.lstrip()
            if line: qttl.append(line)
    if qttl: quotes.append('::'.join(qttl))
    return quotes


_usd = '$'
_gbp = unichr(0x00a3).encode('utf_8')
_eur = unichr(0x20ac).encode('utf_8')
def getBusiness(lines):
    """Movie's business information."""
    bd = _parseColonList(lines, _bus)
    for k in bd.keys():
        nv = []
        for v in bd[k]:
            v = v.replace('USD ',_usd).replace('GBP ',_gbp).replace('EUR',_eur)
            nv.append(v)
        bd[k] = nv
    return bd


def getLaserDisc(lines):
    """Laserdisc information."""
    d = _parseColonList(lines, _ldk)
    for k, v in d.iteritems():
        d[k] = ' '.join(v)
    return d


def getLiterature(lines):
    """Movie's literature information."""
    return _parseColonList(lines, _lit)


_mpaa = {'RE': 'mpaa'}
def getMPAA(lines):
    """Movie's mpaa information."""
    d = _parseColonList(lines, _mpaa)
    for k, v in d.iteritems():
        d[k] = ' '.join(v)
    return d


def nmmvFiles(fp, funct, fname):
    """Files with sections separated by 'MV: ' or 'NM: '."""
    count = 0
    sqlsP = (PersonInfo, ['personID', 'infoTypeID', 'info', 'note'])
    sqlsM = (MovieInfo, ['movieID', 'infoTypeID', 'info', 'note'])

    if fname == 'biographies.list.gz':
        datakind = 'person'
        sqls = sqlsP
        guestid = RoleType.select(RoleType.q.role == 'guest')[0].id
        roleid = str(guestid)
        guestdata = SQLData(table=CastInfo,
                cols=['personID', 'movieID', 'personRole', 'note',
                RawValue('roleID', roleid)], flushEvery=10000)
        akanamesdata = SQLData(table=AkaName, cols=['personID', 'name',
                'imdbIndex', 'namePcodeCf', 'namePcodeNf', 'surnamePcode'])
    else:
        datakind = 'movie'
        sqls = sqlsM
        guestdata = None
        akanamesdata = None
    sqldata = SQLData(table=sqls[0], cols=sqls[1])
    if fname == 'plot.list.gz': sqldata.flushEvery = 1000
    elif fname == 'literature.list.gz': sqldata.flushEvery = 5000
    elif fname == 'business.list.gz': sqldata.flushEvery = 10000
    elif fname == 'biographies.list.gz': sqldata.flushEvery = 5000
    _ltype = type([])
    for ton, text in fp.getByNMMVSections():
        ton = ton.strip()
        if not ton: continue
        note = None
        if datakind == 'movie':
            mopid = CACHE_MID.addUnique(ton)
        else: mopid = CACHE_PID.addUnique(ton)
        if count % 6000 == 0:
            print 'SCANNING %s:' % fname[:-8].replace('-', ' '),
            print _(ton)
        d = funct(text.split('\n'))
        for k, v in d.iteritems():
            if k != 'notable tv guest appearances':
                theid = INFO_TYPES.get(k)
                if theid is None:
                    print 'WARNING key "%s" of ToN' % k,
                    print _(ton),
                    print 'not in INFO_TYPES'
                    continue
            if type(v) is _ltype:
                for i in v:
                    if k == 'notable tv guest appearances':
                        # Put "guest" information in the cast table; these
                        # are a list of Movie object (yes, imdb.Movie.Movie)
                        # FIXME: no more used?
                        title = i.get('long imdb canonical title')
                        if not title: continue
                        movieid = CACHE_MID.addUnique(title)
                        guestdata.add((mopid, movieid, i.currentRole or None,
                                        i.notes or None))
                        continue
                    if k in ('plot', 'mini biography'):
                        s = i.split('::')
                        if len(s) == 2:
                            if note: note += ' '
                            else: note = ''
                            note += '(author: %s)' % s[0]
                            i = s[1]
                    if i: sqldata.add((mopid, theid, i, note))
                    note = None
            else:
                if v: sqldata.add((mopid, theid, v, note))
            if k in ('nick names', 'birth name') and v:
                # Put also the birth name/nick names in the list of aliases.
                if k == 'birth name': realnames = [v]
                else: realnames = v
                for realname in realnames:
                    imdbIndex = re_nameImdbIndex.findall(realname) or None
                    if imdbIndex:
                        imdbIndex = imdbIndex[0]
                        realname = re_nameImdbIndex.sub('', realname)
                    if realname:
                        # XXX: check for duplicates?
                        ##if k == 'birth name':
                        ##    realname = canonicalName(realname)
                        ##else:
                        ##    realname = normalizeName(realname)
                        namePcodeCf, namePcodeNf, surnamePcode = \
                                    name_soundexes(realname)
                        akanamesdata.add((mopid, realname, imdbIndex,
                                    namePcodeCf, namePcodeNf, surnamePcode))
        count += 1
    if guestdata is not None: guestdata.flush()
    if akanamesdata is not None: akanamesdata.flush()
    sqldata.flush()


def doNMMVFiles():
    """Files with large sections, about movies and persons."""
    for fname, start, funct in [('biographies.list.gz',BIO_START,_parseBiography),
            ('business.list.gz',BUS_START,getBusiness),
            ('laserdisc.list.gz',LSD_START,getLaserDisc),
            ('literature.list.gz',LIT_START,getLiterature),
            ('mpaa-ratings-reasons.list.gz',MPAA_START,getMPAA),
            ('plot.list.gz',PLOT_START,getPlot)]:
    ##for fname, start, funct in [('business.list.gz',BUS_START,getBusiness)]:
        try:
            fp = SourceFile(fname, start=start)
        except IOError:
            continue
        if fname == 'literature.list.gz': fp.set_stop(LIT_STOP)
        elif fname == 'business.list.gz': fp.set_stop(BUS_STOP)
        nmmvFiles(fp, funct, fname)
        fp.close()
        t('doNMMVFiles(%s)' % fname[:-8].replace('-', ' '))


def doMiscMovieInfo():
    """Files with information on a single line about movies."""
    sqldata = SQLData(table=MovieInfo,
                cols=['movieID', 'infoTypeID', 'info', 'note'])
    for dataf in (('certificates.list.gz',CER_START),
                    ('color-info.list.gz',COL_START),
                    ('countries.list.gz',COU_START),
                    ('distributors.list.gz',DIS_START),
                    ('genres.list.gz',GEN_START),
                    ('keywords.list.gz',KEY_START),
                    ('language.list.gz',LAN_START),
                    ('locations.list.gz',LOC_START),
                    ('miscellaneous-companies.list.gz',MIS_START),
                    ('production-companies.list.gz',PRO_START),
                    ('running-times.list.gz',RUN_START),
                    ('sound-mix.list.gz',SOU_START),
                    ('special-effects-companies.list.gz',SFX_START),
                    ('technical.list.gz',TCN_START),
                    ('release-dates.list.gz',RELDATE_START)):
        try:
            fp = SourceFile(dataf[0], start=dataf[1])
        except IOError:
            continue
        typeindex = dataf[0][:-8].replace('-', ' ')
        if typeindex == 'running times': typeindex = 'runtimes'
        elif typeindex == 'technical': typeindex = 'tech info'
        elif typeindex == 'language': typeindex = 'languages'
        infoid =  INFO_TYPES[typeindex]
        count = 0
        if dataf[0] in ('distributors.list.gz', 'locations.list.gz',
                        'miscellaneous-companies.list.gz'):
            sqldata.flushEvery = 10000
        else:
            sqldata.flushEvery = 20000
        for line in fp:
            data = unpack(line.strip(), ('title', 'info', 'note'))
            if not data.has_key('title'): continue
            if not data.has_key('info'): continue
            title = data['title']
            mid = CACHE_MID.addUnique(title)
            note = None
            if data.has_key('note'):
                note = data['note']
            if count % 10000 == 0:
                print 'SCANNING %s:' % dataf[0][:-8].replace('-', ' '),
                print _(data['title'])
            sqldata.add((mid, infoid, data['info'], note))
            count += 1
        sqldata.flush()
        fp.close()
        t('doMiscMovieInfo(%s)' % dataf[0][:-8].replace('-', ' '))


def getRating():
    """Movie's rating."""
    try: fp = SourceFile('ratings.list.gz', start=RAT_START, stop=RAT_STOP)
    except IOError: return
    sqldata = SQLData(table=MovieInfo, cols=['movieID', 'infoTypeID', 'info'])
    count = 0
    for line in fp:
        data = unpack(line, ('votes distribution', 'votes', 'rating', 'title'),
                        sep='  ')
        if not data.has_key('title'): continue
        title = data['title'].strip()
        mid = CACHE_MID.addUnique(title)
        if count % 10000 == 0:
                print 'SCANNING rating:', _(title)
        sqldata.add((mid, INFO_TYPES['votes distribution'],
                    data.get('votes distribution')))
        sqldata.add((mid, INFO_TYPES['votes'], data.get('votes')))
        sqldata.add((mid, INFO_TYPES['rating'], data.get('rating')))
        count += 1
    sqldata.flush()
    fp.close()


def getTopBottomRating():
    """Movie's rating, scanning for top 250 and bottom 100."""
    for what in ('top 250 rank', 'bottom 10 rank'):
        if what == 'top 250 rank': st = RAT_TOP250_START
        else: st = RAT_BOT10_START
        try: fp = SourceFile('ratings.list.gz', start=st, stop=TOPBOT_STOP)
        except IOError: break
        sqldata = SQLData(table=MovieInfo,
                    cols=['movieID', 'infoTypeID',
                    RawValue('info', str(INFO_TYPES[what]))])
        count = 1
        print 'SCANNING %s...' % what
        for line in fp:
            data = unpack(line, ('votes distribution', 'votes', 'rank',
                            'title'), sep='  ')
            if not data.has_key('title'): continue
            title = data['title'].strip()
            mid = CACHE_MID.addUnique(title)
            if what == 'top 250 rank': rank = count
            else: rank = 11 - count
            sqldata.add((mid, rank))
            count += 1
        sqldata.flush()
        fp.close()


def getPlot(lines):
    """Movie's plot."""
    plotl = []
    plotlappend = plotl.append
    plotltmp = []
    plotltmpappend = plotltmp.append
    for line in lines:
        linestart = line[:4]
        if linestart == 'PL: ':
            plotltmpappend(line[4:])
        elif linestart == 'BY: ':
            plotlappend('%s::%s' % (line[4:].strip(), ' '.join(plotltmp)))
            plotltmp[:] = []
    return {'plot': plotl}


def completeCast():
    """Movie's complete cast/crew information."""
    CCKind = {}
    for x in CompCastType.select():
        CCKind[x.kind] = x.id
    for fname, start in [('complete-cast.list.gz',COMPCAST_START),
                        ('complete-crew.list.gz',COMPCREW_START)]:
        try:
            fp = SourceFile(fname, start=start, stop=COMP_STOP)
        except IOError:
            continue
        if fname == 'complete-cast.list.gz': obj = 'cast'
        else: obj = 'crew'
        subID = str(CCKind[obj])
        sqldata = SQLData(table=CompleteCast,
                cols=['movieID', RawValue('subjectID', subID),
                'statusID'])
        count = 0
        for line in fp:
            ll = [x for x in line.split('\t') if x]
            if len(ll) != 2: continue
            title = ll[0]
            mid = CACHE_MID.addUnique(title)
            if count % 10000 == 0:
                print 'SCANNING %s:' % fname[:-8].replace('-', ' '),
                print _(title)
            sqldata.add((mid, str(CCKind[ll[1].lower().strip()])))
            count += 1
        fp.close()
        sqldata.flush()


# global instances
CACHE_MID = MoviesCache()
CACHE_PID = PersonsCache()

INFO_TYPES = {}
for x in InfoType.select():
    INFO_TYPES[x.info] = x.id


def _cmpfunc(x, y):
    """Sort a list of tuples, by the length of the first item (in reverse)."""
    lx = len(x[0])
    ly = len(y[0])
    if lx > ly: return -1
    elif lx < ly: return 1
    return 0

MOVIELINK_IDS = []
for x in LinkType.select():
    MOVIELINK_IDS.append((x.link, len(x.link), x.id))
MOVIELINK_IDS.sort(_cmpfunc)

KIND_IDS = {}
KIND_STRS = {}
for x in KindType.select():
    KIND_IDS[x.kind] = x.id
    KIND_STRS[x.id] = x.kind


CCAST_TYPES = {}
for x in CompCastType.select():
    CCAST_TYPES[x.kind] = x.id


# begin the iterations...
def run():
    print 'RUNNING imdbpy2sql.py'
    # Populate the CACHE_MID instance.
    readMovieList()
    ##CACHE_MID.populate()
    ##CACHE_PID.populate()
    t('readMovieList()')


    # actors, actresses, directors, ....
    castLists()

    doAkaNames()
    t('doAkaNames()')
    doAkaTitles()
    t('doAkaTitles()')
    doMinusHashFiles()
    t('doMinusHashFiles()')

    doNMMVFiles()

    doMiscMovieInfo()
    doMovieLinks()
    t('doMovieLinks()')

    getRating()
    t('getRating()')
    getTaglines()
    t('getTaglines()')
    getTopBottomRating()
    t('getTopBottomRating()')
    completeCast()
    t('completeCast()')

    # Flush caches.
    CACHE_MID.flush()
    CACHE_PID.flush()

    print 'DONE! (in %d minutes, %d seconds)' % \
            divmod(int(time.time())-BEGIN_TIME, 60)


_HEARD = 0
def _kdb_handler(signum, frame):
    """Die gracefully."""
    global _HEARD
    if _HEARD:
        print "EHI!  DON'T PUSH ME!  I'VE HEARD YOU THE FIRST TIME! :-)"
        return
    print 'INTERRUPT REQUEST RECEIVED FROM USER.  FLUSHING CACHES...'
    _HEARD = 1
    # XXX: trap _every_ error?
    try: CACHE_MID.flush()
    except IntegrityError: pass
    try: CACHE_PID.flush()
    except IntegrityError: pass
    print 'DONE! (in %d minutes, %d seconds)' % \
            divmod(int(time.time())-BEGIN_TIME, 60)
    sys.exit()


if __name__ == '__main__':
    import signal
    signal.signal(signal.SIGINT, _kdb_handler)
    run()

