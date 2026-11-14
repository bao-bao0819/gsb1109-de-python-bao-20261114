Calibrating frame-only MicroDVD transcripts
===========================================

MicroDVD files (``.sub``) store each cue as a pair of *frame numbers*, for
example ``{360}{435}Second cue.``. Turning those frames into wall-clock times
requires the frame-rate of the source video. When the usual frame-rate
declaration line is missing and the video also has an unknown *lead-in offset*
(frame 0 is not necessarily broadcast time 0), there are two unknowns:

* the frame-rate ``fps``,
* the lead-in offset in milliseconds, where
  ``broadcast_time = frame_time + offset``.

The calibration tools in :mod:`pysubs2.calibration` resolve both from a
registration table: a handful of cues with their approximate broadcast times,
each expressed as an accepted time *window*.

How feasibility is computed
---------------------------

For a cue with start frame ``F`` matched to a broadcast window
``[expected - tolerance, expected + tolerance]`` (all in milliseconds),
a candidate frame-rate gives the frame time ``round(F * 1000 / fps)``.
The offset must then satisfy::

    (expected - tolerance) - frame_time <= offset <= (expected + tolerance) - frame_time

Each check therefore constrains the offset to an interval. For a given
frame-rate, the intervals of all retained checks are intersected; a non-empty
intersection means the frame-rate is feasible, and the intersection is exactly
the range of lead-in offsets that satisfy every window at once.

Every candidate frame-rate is evaluated independently and **all** feasible
interpretations are returned -- the result is never reduced to a single picked
answer. Two frame-rates that both fit the windows are reported side by side so
the import operator can compare their offset ranges.

Suspicious checks
-----------------

A registration entry may be flagged *suspicious*. Suspicious checks are first
used like any other, and then additionally tried one at a time with that single
check excluded. Each exclusion is reported as its own interpretation and names
the exact check that was dropped. Non-suspicious checks are never excluded, and
no interpretation ever drops more than one check.

Command-line usage
------------------

The entry point is :mod:`pysubs2.calibration`::

    python -m pysubs2.calibration transcript.sub \
        --fps 23.976 --fps 24 --fps 25 \
        --check "360:0:00:16.4,60" \
        --check "720:0:00:30.8,60"

Each ``--check`` has the form ``FRAME:EXPECTED[,TOLERANCE][?]``:

* ``FRAME`` is the cue start frame from the MicroDVD file (the nearest actual
  cue frame is used if there is no cue exactly at that frame, and the report
  calls this out);
* ``EXPECTED`` is the approximate broadcast time, either a millisecond count
  or an ``H:MM:SS[.mmm]`` timestamp;
* ``TOLERANCE`` is the window half-width in milliseconds (default ``0``);
* a trailing ``?`` marks the check as suspicious, for example
  ``--check "1080:0:01:00,500?"``.

Repeat ``--fps`` for every candidate frame-rate and ``--check`` for every
registration entry. Checks can also be supplied one per line (``#`` comments
allowed) through ``--checks-file``. The command exits non-zero when no
frame-rate satisfies the windows, even after excluding one suspicious check.

Python API
----------

.. code-block:: python

    from pysubs2.calibration import (
        calibrate_microdvd, make_timing_checks, format_calibration_report,
    )

    checks = make_timing_checks(
        [
            (360, 16400, 60),
            (720, 30800, 60),
            (1080, 60000, 500, "doubtful cue"),
        ],
        suspicious=[2],
    )

    results = calibrate_microdvd(
        "transcript.sub",
        candidate_fps=[23.976, 24.0, 25.0],
        timing_checks=checks,
    )

    for result in results:
        print(result.fps, result.offset_range, result.excluded)

    print(format_calibration_report(results, checks))

:func:`~pysubs2.calibration.calibrate_microdvd` accepts either a file path or
an already extracted sequence of cue start frames (see
:func:`~pysubs2.calibration.read_microdvd_frames`).
