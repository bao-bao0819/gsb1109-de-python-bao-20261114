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

Checkpointed decoding
^^^^^^^^^^^^^^^^^^^^^

Decoding always restarts from the first character. For a long string that is
read back repeatedly, a single scan can build a ``PolylineIndex`` holding the
minimum number of checkpoints needed to keep every single-point read within a
caller-supplied budget::

    from polyline import PolylineIndex

    index = PolylineIndex.build(expression, max_read=64)
    index.point_count          # number of points in the polyline
    index.checkpoints          # persisted parser state
    index.decode_point(120)    # one ranged read of at most 64 characters
    index.decode_slice(100, 110)

Checkpoints only store point indices, character offsets and the accumulated
integer coordinates, so they can be serialized with ``to_dict`` and reattached
to any storage backend that implements ranged reads::

    from polyline import PolylineIndex, StringRange

    rebuilt = PolylineIndex.from_source(
        StringRange(expression), list(index.checkpoints),
        index.point_count, len(expression),
    )

A slice is read sequentially on top of the same checkpoint addressing; each
individual ranged read still stays within ``max_read``. If one point's own
encoding is longer than the budget, ``build`` raises
``OversizedPointError`` naming the point, since no checkpoint can split a
point's encoding into smaller reads.


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

