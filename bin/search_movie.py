#!/usr/bin/env python
"""
search_movie.py

Usage: search_movie "movie title"

Search for the given title and print the results.
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
    print '  %s "movie title"' % sys.argv[0]
    sys.exit(2)

title = sys.argv[1]


i = imdb.IMDb()

try:
    # Do the search, and get the results (a list of Movie objects).
    results = i.search_movie(title)
except imdb.IMDbError, e:
    print "Probably you're not connected to Internet.  Complete error report:"
    print e
    sys.exit(3)

# Print the results.
print '    %s results for "%s":' % (len(results), title)

# Print the long imdb title for every movie.
for movie in results:
    print '%s: %s' % (i.get_imdbMovieID(movie.movieID),
                        movie['long imdb title'])


