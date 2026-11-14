=============
Feedgenerator
=============

This module can be used to generate web feeds in both ATOM and RSS format. It
has support for extensions. Included is for example an extension to produce
Podcasts.

It is licensed under the terms of both, the FreeBSD license and the LGPLv3+.
Choose the one which is more convenient for you. For more details have a look
at license.bsd and license.lgpl.

More details about the project:

- `Repository <https://github.com/lkiesow/python-feedgen>`_
- `Documentation <https://lkiesow.github.io/python-feedgen/>`_
- `Python Package Index <https://pypi.python.org/pypi/feedgen/>`_


------------
Installation
------------

**Prebuild packages**

If your distribution includes this project as package, like Fedora Linux does,
you can simply use your package manager to install the package. For example::

    $ dnf install python3-feedgen


**Using pip**

You can also use pip to install the feedgen module. Simply run::

    $ pip install feedgen


-------------
Create a Feed
-------------

To create a feed simply instantiate the FeedGenerator class and insert some
data:

.. code-block:: python

    from feedgen.feed import FeedGenerator
    fg = FeedGenerator()
    fg.id('http://lernfunk.de/media/654321')
    fg.title('Some Testfeed')
    fg.author( {'name':'John Doe','email':'john@example.de'} )
    fg.link( href='http://example.com', rel='alternate' )
    fg.logo('http://ex.com/logo.jpg')
    fg.subtitle('This is a cool feed!')
    fg.link( href='http://larskiesow.de/test.atom', rel='self' )
    fg.language('en')

Note that for the methods which set fields that can occur more than once in a
feed you can use all of the following ways to provide data:

- Provide the data for that element as keyword arguments
- Provide the data for that element as dictionary
- Provide a list of dictionaries with the data for several elements

Example:

.. code-block:: python

    fg.contributor( name='John Doe', email='jdoe@example.com' )
    fg.contributor({'name':'John Doe', 'email':'jdoe@example.com'})
    fg.contributor([{'name':'John Doe', 'email':'jdoe@example.com'}, ...])

-----------------
Generate the Feed
-----------------

After that you can generate both RSS or ATOM by calling the respective method:

.. code-block:: python

    atomfeed = fg.atom_str(pretty=True) # Get the ATOM feed as string
    rssfeed  = fg.rss_str(pretty=True) # Get the RSS feed as string
    fg.atom_file('atom.xml') # Write the ATOM feed to a file
    fg.rss_file('rss.xml') # Write the RSS feed to a file


----------------
Add Feed Entries
----------------

To add entries (items) to a feed you need to create new FeedEntry objects and
append them to the list of entries in the FeedGenerator. The most convenient
way to go is to use the FeedGenerator itself for the instantiation of the
FeedEntry object:

.. code-block:: python

    fe = fg.add_entry()
    fe.id('http://lernfunk.de/media/654321/1')
    fe.title('The First Episode')
    fe.link(href="http://lernfunk.de/feed")

The FeedGenerator's method `add_entry(...)` will generate a new FeedEntry
object, automatically append it to the feeds internal list of entries and
return it, so that additional data can be added.

--------------------
Migrate RSS to Atom
--------------------

When an existing RSS feed is replaced by Atom, downstream readers often treat
every historical article as new because the identifier changed. Keep an
explicit mapping from the historical RSS guid to the new Atom id instead of
guessing by link:

.. code-block:: python

    from feedgen.feed import FeedGenerator
    from feedgen.migration import resolve_mappings

    # Each record may use legacy_guid/atom_id, old_guid/new_id, or a simple
    # {"old-guid": "new-id"} dictionary.
    plan = resolve_mappings(
        {'old-guid-1': 'https://example.com/posts/1'},
        [{'legacy_guid': 'old-guid-2',
          'atom_id': 'https://example.com/posts/2'}],
    )
    print(plan.conflict_report())

    fg = FeedGenerator()
    ...
    fe = fg.add_entry()
    fe.title('A historical article')
    fe.id('https://example.com/posts/1')    # new Atom id
    fe.legacy_guid('old-guid-1')            # historical RSS guid

The entry keeps both values: ``atom_str`` emits the new ``<id>`` while
``rss_str`` emits the historical ``<guid>``. Calling ``id(...)`` and
``guid(...)`` in either order gives the same result.

The whole mapping can also be applied to a feed. Entries are matched by their
existing Atom id or RSS guid. ``link_hint=True`` allows a link to be used as a
fallback for an otherwise unidentified entry, but a link is never treated as
proof of identity because a repost can share the original link:

.. code-block:: python

    plan = fg.apply_guid_mappings(
        'first-export.json',
        'second-export.csv',
        link_hint=True,
    )
    for conflict in plan.conflict_groups:
        print(conflict)  # one group per historical guid that was claimed twice

If two migration materials claim different new Atom ids for the same
historical guid, that guid is reported as a conflict group and is not
published. Every other agreed mapping is still applied. Because resolution
sorts by identifiers rather than input order, reversing the order of the
materials cannot change the resulting article count.

A resolved plan can be inspected from the command line:

.. code-block:: console

    $ python -m feedgen.migration first.json second.json

----------
Extensions
----------
The FeedGenerator supports extensions to include additional data into the XML
structure of the feeds. Extensions can be loaded like this:

.. code-block:: python

    fg.load_extension('someext', atom=True, rss=True)

This example would try to load the extension “someext” from the file
`ext/someext.py`.  It is required that `someext.py` contains a class named
“SomextExtension” which is required to have at least the two methods
`extend_rss(...)` and `extend_atom(...)`. Although not required, it is strongly
suggested to use `BaseExtension` from `ext/base.py` as superclass.

`load_extension('someext', ...)` will also try to load a class named
“SomextEntryExtension” for every entry of the feed. This class can be located
either in the same file as SomextExtension or in `ext/someext_entry.py` which
is suggested especially for large extensions.

The parameters `atom` and `rss` control if the extension is used for ATOM and
RSS feeds respectively. The default value for both parameters is `True`,
meaning the extension is used for both kinds of feeds.

**Example: Producing a Podcast**

One extension already provided is the podcast extension. A podcast is an RSS
feed with some additional elements for ITunes.

To produce a podcast simply load the `podcast` extension:

.. code-block:: python

    from feedgen.feed import FeedGenerator
    fg = FeedGenerator()
    fg.load_extension('podcast')
    ...
    fg.podcast.itunes_category('Technology', 'Podcasting')
    ...
    fe = fg.add_entry()
    fe.id('http://lernfunk.de/media/654321/1/file.mp3')
    fe.title('The First Episode')
    fe.description('Enjoy our first episode.')
    fe.enclosure('http://lernfunk.de/media/654321/1/file.mp3', 0, 'audio/mpeg')
    ...
    fg.rss_str(pretty=True)
    fg.rss_file('podcast.xml')

If the FeedGenerator class is used to load an extension, it is automatically
loaded for every feed entry as well.  You can, however, load an extension for a
specific FeedEntry only by calling `load_extension(...)` on that entry.

Even if extensions are loaded, they can be temporarily disabled during the feed
generation by calling the generating method with the keyword argument
`extensions` set to `False`.

**Custom Extensions**

If you want to load custom extensions which are not part of the feedgen
package, you can use the method `register_extension` instead. You can directly
pass the classes for the feed and the entry extension to this method meaning
that you can define them everywhere.


---------------------
Testing the Generator
---------------------

You can test the module by simply executing::

    $ python -m feedgen

If you want to have a look at the code for this test to have a working code
example for a whole feed generation process, you can find it in the
`__main__.py <https://github.com/lkiesow/python-feedgen/blob/master/feedgen/__main__.py>`_.
