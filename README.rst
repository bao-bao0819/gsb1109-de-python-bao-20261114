polyline
========

.. image:: http://img.shields.io/pypi/v/polyline.svg?style=flat
    :target: https://pypi.python.org/pypi/polyline/
.. image:: https://readthedocs.org/projects/polyline/badge/?version=latest
    :target: https://polyline.readthedocs.io/en/latest/?badge=latest
    :alt: Documentation Status
.. image:: https://github.com/frederickjansen/polyline/actions/workflows/lint-test-docs.yml/badge.svg
    :target: https://github.com/frederickjansen/polyline/actions/workflows/lint-test-docs.yml
    :alt: Build

``polyline`` is a Python implementation of Google's `Encoded Polyline Algorithm
Format <https://developers.google.com/maps/documentation/utilities/polylinealgorithm>`__. It is essentially a port of `Mapbox polyline <https://github.com/mapbox/polyline>`__ with some additional features.

Installation
------------

``polyline`` can be installed using ``pip``::

    $ pip install polyline

Starting from ``v2.0.0`` only Python 3.7 and above is supported. For Python 2 support, please install ``v1.4.0``::

    $ pip install polyline==1.4.0

API Documentation
-----------------

Encoding
^^^^^^^^

To get the encoded polyline representation of a given set of (lat, lon) coordinates::

    import polyline
    polyline.encode([(38.5, -120.2), (40.7, -120.9), (43.2, -126.4)], 5)

This should return ``_p~iF~ps|U_ulL~ugC_hgN~eq`@``.

You can set the required precision with the optional ``precision`` parameter. The default value is 5.

You can encode (lon, lat) tuples by setting ``geojson=True``.

Decoding
^^^^^^^^

To get a set of coordinates represented by a given encoded polyline string::

    import polyline
    polyline.decode('u{~vFvyys@fS]', 5)

This should return ``[(40.63179, -8.65708), (40.62855, -8.65693)]`` in (lat, lon) order.

You can set the required precision with the optional ``precision`` parameter. The default value is 5.

You can decode into (lon, lat) tuples by setting ``geojson=True``.


Merging fragments
^^^^^^^^^^^^^^^^^

Devices often record one route as several numbered fragments. The fragments may
arrive in any order and in either direction, and a hand-off may share more than
one sample point. ``merge`` reconstructs the shortest point sequence that still
contains every fragment as a contiguous run::

    import polyline
    polyline.merge({
        1: [(38.500, -120.200), (40.700, -120.950)],
        2: [(43.252, -126.453), (40.700, -120.950)],
    })

This returns ``[(38.5, -120.2), (40.7, -120.95), (43.252, -126.453)]``. The
second fragment is reversed automatically. Fragments may also be encoded
polylines, so a batch can be merged without decoding it manually::

    polyline.merge([polyline.encode(first), polyline.encode(second)])
    polyline.merge_encoded([first, second])

A batch holds at most 12 fragments (``polyline.MAX_MERGE_FRAGMENTS``). Only the
longest shared suffix/prefix of each junction is collapsed, and a multi-point
fragment is never swallowed whole, so a genuine out-and-back leg keeps both
visits to every repeated point. Passing ``include_reversals=True`` keeps routes
that differ only by reversing the whole route, which is otherwise collapsed.

To disambiguate such a reversal, pass ``start_point`` (also accepted as
``start`` or ``origin``); the direction that actually starts there is put
first. When two different shortest routes are possible, for example because the
same intersection is visited twice, ``merge_candidates`` returns both. Each
candidate records its placements and can be audited::

    candidates = polyline.merge_candidates(segments)
    for candidate in candidates:
        assert candidate.verify(segments)
        candidate.points        # merged sequence
        candidate.fragment_ids  # order used
        candidate.flipped       # per-fragment reversal flags
        candidate.placements    # (fragment_id, start, stop) slices


Development
-----------
All installation and development dependencies are fully specified in ``pyproject.toml``. The ``project.optional-dependencies`` object is used to `specify optional requirements <https://setuptools.pypa.io/en/latest/userguide/pyproject_config.html>`__ for various development tasks. This makes it possible to specify additional options when performing installation using ``pip``::

    python -m pip install .[dev]

Documentation
^^^^^^^^^^^^^
The documentation can be generated automatically from the source files using `Sphinx <https://www.sphinx-doc.org>`__::

    python -m sphinx.cmd.build -b html docs docs/_build/html

Testing and Conventions
^^^^^^^^^^^^^^^^^^^^^^^
All unit tests are executed and their coverage is measured when using `pytest <https://docs.pytest.org>`__::

    python -m pytest

Style conventions are enforced using `Pylint <https://pylint.pycqa.org>`__::

    python -m pylint polyline

Contributions
^^^^^^^^^^^^^
In order to contribute to the source code, open an issue or submit a pull request on the `GitHub page <https://github.com/frederickjansen/polyline>`__ for this library.

Versioning
^^^^^^^^^^
Beginning with version 0.1.0, the version number format for this library and the changes to the library associated with version number increments conform with `Semantic Versioning 2.0.0 <https://semver.org/#semantic-versioning-200>`__.

Publishing
^^^^^^^^^^
This library can be published as a `package on PyPI <https://pypi.org/project/polyline>`__ by a package maintainer. Ensure that the correct version number appears in ``pyproject.toml``, and that any links in this README document to the Read the Docs documentation of this package (or its dependencies) have appropriate version numbers. Also ensure that the Read the Docs project for this library has an `automation rule <https://docs.readthedocs.io/en/stable/automation-rules.html>`__ that activates and sets as the default all tagged versions. Create and push a tag for this version (replacing ``?.?.?`` with the version number)::

    git tag ?.?.?
    git push origin ?.?.?

Remove any old build/distribution files. Then, package the source into a distribution archive::

    rm -rf build dist src/*.egg-info
    python -m build --sdist --wheel .

Finally, upload the package distribution archive to `PyPI <https://pypi.org>`__::

    python -m twine upload dist/*

