#!/usr/bin/env python
"""
search_person.py

Usage: search_person "person name"

Search for the given name and print the results.
"""

import sys

# Import the IMDbPY package.
try:
    import imdb
except ImportError:
    print 'You bad boy!  You need to install the IMDbPY package!'
    sys.exit(1)


if len(sys.argv) != 2:
    print 'Only one argument is required:'
    print '  %s "person name"' % sys.argv[0]
    sys.exit(2)

name = sys.argv[1]


i = imdb.IMDb()

try:
    # Do the search, and get the results (a list of Person objects).
    results = i.search_person(name)
except imdb.IMDbError, e:
    print "Probably you're not connected to Internet.  Complete error report:"
    print e
    sys.exit(3)

# Print the results.
print '    %s results for "%s":' % (len(results), name)

# Print the long imdb name for every person.
for person in results:
    print '%s: %s' % (i.get_imdbPersonID(person.personID),
                        person['long imdb name'])


