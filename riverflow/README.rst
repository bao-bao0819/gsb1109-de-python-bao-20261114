riverflow
=========

Daily river-discharge reconciliation over two GeoJSON layers:

* a **station layer** of Point features with stable ids, and
* a **reach layer** of LineString features whose properties name the two
  station ids they connect (``from`` / ``to``, ``source`` / ``target``,
  ``upstream`` / ``downstream``, ``起点`` / ``终点`` ...).

Model
-----

Every station is conservative except stations explicitly marked as an
``inflow`` or ``outflow`` boundary.  Readings may be exact values or
intervals; a station with no reading is simply unconstrained on that
quantity.

* Boundary readings bound the external exchange (inflow injection or
  outflow abstraction).
* Interior station readings (``through`` / ``total_flow`` / ``过流量`` ...)
  bound the total flow passing through, which is how a fork that "only
  knows the total" is expressed.
* Reach readings bound that reach's own flow.

Each station is split into an ``in``/``out`` pair joined by a station arc,
so every constraint becomes a boxed arc in a circulation network.
Feasibility is Hoffman's super-source/super-sink max-flow construction;
each reach's independently attainable minimum and maximum are then found
with rerouting max-flows on the residual network.  The two branches of a
fork therefore each report their *own* maximum — nothing claims both can
be maximal simultaneously.

Weakly connected components are solved independently, so one inconsistent
water system never blocks the others.

Output
------

Every reconciled feature gains:

* ``flow_min`` / ``flow_max`` — the tightest attainable interval
  (``null`` when unbounded),
* ``reconcile_status`` — ``ok``, ``infeasible`` or ``error``,
* ``reconcile_error`` — on failure, a message naming the violating
  station set, the crossing reaches, the shortfall ``gap`` and the
  irreducible constraint core responsible.

CLI
---

.. code-block:: console

    python -m riverflow input.geojson output.geojson --report report.json

The input may be a single mixed FeatureCollection, an array of
FeatureCollections, or an object with ``stations`` and ``reaches`` keys.
``--strict`` makes the process exit non-zero when any component is
infeasible or structurally invalid.

Library
-------

.. code-block:: python

    from riverflow import load_layers, reconcile_layers

    stations, reaches = load_layers("river.geojson")
    report = reconcile_layers(stations, reaches)
