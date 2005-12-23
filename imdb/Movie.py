"""
Movie module (imdb package).

This module provides the Movie class, used to store information about
a given movie.

Copyright 2004, 2005 Davide Alberani <da@erlug.linux.it>

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

import types
from copy import deepcopy
from utils import analyze_title, build_title, normalizeTitle, _Container


class Movie(_Container):
    """A Movie.

    Every information about a movie can be accessed as:
        movieObject['information']
    to get a list of the kind of information stored in a
    Movie object, use the keys() method; some useful aliases
    are defined (as "plot summary" for the "plot" key); see the
    keys_alias dictionary.
    """
    # The default sets of information retrieved.
    default_info = ('main', 'plot')

    # Aliases for some not-so-intuitive keys.
    keys_alias = {
                'user rating':  'rating',
                'plot summary': 'plot',
                'plot summaries': 'plot',
                'directed by':  'director',
                'writing credits': 'writer',
                'produced by':  'producer',
                'original music by':    'composer',
                'original music':    'composer',
                'non-original music by':    'composer',
                'non-original music':    'composer',
                'music':    'composer',
                'cinematography by':    'cinematographer',
                'cinematography':   'cinematographer',
                'film editing by':  'editor',
                'film editing': 'editor',
                'editing':  'editor',
                'actors':   'cast',
                'actresses':    'cast',
                'casting by':   'casting director',
                'casting':  'casting director',
                'art direction by': 'art director',
                'art direction': 'art director',
                'set decoration by':    'set decorator',
                'set decoration':   'set decorator',
                'costume design by':    'costume designer',
                'costume design':    'costume designer',
                'makeup department':    'make up',
                'makeup':    'make up',
                'make-up':    'make up',
                'production management':    'production manager',
                'second unit director or assistant director':
                                                'assistant director',
                'second unit director':   'assistant director',
                'sound department': 'sound crew',
                'special effects by':   'special effects',
                'visual effects by':    'visual effects',
                'stunts':   'stunt performer',
                'other crew':   'miscellaneous crew',
                'misc crew':   'miscellaneous crew',
                'miscellaneouscrew':   'miscellaneous crew',
                'crewmembers': 'miscellaneous crew',
                'crew members': 'miscellaneous crew',
                'other companies':  'miscellaneous companies',
                'misc companies': 'miscellaneous companies',
                'aka':  'akas',
                'also known as':    'akas',
                'country':  'countries',
                'runtime':  'runtimes',
                'lang': 'languages',
                'language': 'languages',
                'certificate':  'certificates',
                'certifications':   'certificates',
                'certification':    'certificates',
                'miscellaneous links':  'misc links',
                'miscellaneous':    'misc links',
                'soundclips':   'sound clips',
                'videoclips':   'video clips',
                'photographs':  'photo sites',
                'guest': 'guests',
                'guest appearances': 'guests',
                'tv guests': 'guests',
                'notable tv guest appearances': 'guests',
                'amazon review': 'amazon reviews'}

    keys_tomodify_list = ('plot', 'trivia', 'alternate versions', 'goofs',
                        'quotes', 'dvd', 'laserdisc', 'news', 'soundtrack')

    def _init(self, **kwds):
        """Initialize a Movie object.
        
        *movieID* -- the unique identifier for the movie.
        *title* -- the title of the Movie, if not in the data dictionary.
        *myTitle* -- your personal title for the movie.
        *myID* -- your personal identifier for the movie.
        *data* -- a dictionary used to initialize the object.
        *currentRole* -- a string representing the current role or duty
                        of a person in this movie.
        *notes* -- notes for the person referred in the currentRole
                    attribute; e.g.: '(voice)'.
        *accessSystem* -- a string representing the data access system used.
        *titlesRefs* -- a dictionary with references to movies.
        *namesRefs* -- a dictionary with references to persons.
        *modFunct* -- function called returning text fields.
        """
        title = kwds.get('title')
        if title and not self.data.has_key('title'):
            self.set_title(title)
        self.movieID = kwds.get('movieID', None)
        self.myTitle = kwds.get('myTitle', '')

    def _reset(self):
        """Reset the Movie object."""
        self.movieID = None
        self.myTitle = ''

    def set_title(self, title):
        """Set the title of the movie."""
        d_title = analyze_title(title, canonical=1)
        self.data.update(d_title)

    def _additional_keys(self):
        """Return a list of valid keys."""
        if self.data.has_key('title'):
            return ['canonical title', 'long imdb title',
                    'long imdb canonical title']
        return []

    def __getitem__(self, key):
        """Return the value for a given key, checking key aliases;
        a KeyError exception is raised if the key is not found.
        """
        if self.data.has_key('title'):
            if key == 'title':
                return normalizeTitle(self.data['title'])
            elif key == 'long imdb title':
                return build_title(self.data, canonical=0)
            elif key == 'canonical title':
                return self.data['title']
            elif key == 'long imdb canonical title':
                return build_title(self.data, canonical=1)
        return _Container.__getitem__(self, key)

    def __nonzero__(self):
        """The Movie is "false" if the self.data does not contains
        a title."""
        # XXX: check the title and the movieID?
        if self.data.has_key('title'): return 1
        return 0

    def isSameTitle(self, other):
        """Return true if this and the compared object have the same
        long imdb title and/or movieID.
        """
        if not isinstance(other, self.__class__): return 0
        if self.data.has_key('title') and \
                other.data.has_key('title') and \
                build_title(self.data, canonical=1) == \
                build_title(other.data, canonical=1):
            return 1
        if self.accessSystem == other.accessSystem and \
                self.movieID is not None and self.movieID == other.movieID:
            return 1
        return 0

    def __contains__(self, item):
        """Return true if the given Person object is listed in this Movie."""
        from Person import Person
        if not isinstance(item, Person):
            return 0
        for i in self.data.values():
            if type(i) in (types.ListType, types.TupleType):
                for j in i:
                    if isinstance(j, Person) and item.isSamePerson(j):
                        return 1
        return 0

    def __deepcopy__(self, memo):
        """Return a deep copy of a Movie instance."""
        m = Movie(title='', movieID=self.movieID, myTitle=self.myTitle,
                    myID=self.myID, data=deepcopy(self.data, memo),
                    currentRole=self.currentRole, notes=self.notes,
                    accessSystem=self.accessSystem,
                    titlesRefs=deepcopy(self.titlesRefs, memo),
                    namesRefs=deepcopy(self.namesRefs, memo))
        m.current_info = list(self.current_info)
        m.set_mod_funct(self.modFunct)
        return m

    def __str__(self):
        """Simply print the short title."""
        return self.get('title', '')

    def summary(self):
        """Return a string with a pretty-printed summary for the movie."""
        if not self:
            return ''
        s = 'Movie\n=====\nTitle: %s\n' % \
                    self.get('long imdb canonical title', '')
        genres = self.get('genres')
        if genres: s += 'Genres: %s.' % ', '.join(genres)
        director = self.get('director')
        if director:
            s += 'Director: '
            for name in director:
                s += str(name)
                if name.currentRole:
                    s += ' (%s)' % name.currentRole
                s += ', '
            s = s[:-2] + '.\n'
        writer = self.get('writer')
        if writer:
            s += 'Writer: '
            for name in writer:
                s += str(name)
                if name.currentRole:
                    s += ' (%s)' % name.currentRole
                s += ', '
            s = s[:-2] + '.\n'
        cast = self.get('cast')
        if cast:
            cast = cast[:5]
            s += 'Cast: '
            for name in cast:
                s += str(name)
                if name.currentRole:
                    s += ' (%s)' % name.currentRole
                s += ', '
            s = s[:-2] + '.\n'
        runtime = self.get('runtimes')
        if runtime:
            s += 'Runtime: '
            for r in runtime:
                s += r + ', '
            s = s[:-2] + '.\n'
        countries = self.get('countries')
        if countries:
            s += 'Country: '
            for c in countries:
                s += c + ', '
            s = s[:-2] + '.\n'
        lang = self.get('languages')
        if lang:
            s += 'Language: '
            for l in lang:
                s += l + ', '
            s = s[:-2] + '.\n'
        rating = self.get('rating')
        if rating:
            s += 'Rating: %s\n' % rating
        nr_votes = self.get('votes')
        if nr_votes:
            s += 'Votes: %s\n' % nr_votes
        plot = self.get('plot')
        if plot:
            plot = plot[0]
            i = plot.find('::')
            if i != -1:
                plot = plot[i+2:]
            s += 'Plot: %s' % plot
        return s


