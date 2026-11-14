# Data validation for ODS imports

`odf.datavalidation` evaluates the `table:content-validation` rules stored in
an OpenDocument spreadsheet against values that are about to be imported.
`odf.validation` adds a file-based convenience wrapper.

## Supported rules

The evaluator handles list validation:

* Constant lists: `of:cell-content-is-in-list("draft","approved")`
* Cell ranges: `of:cell-content-is-in-list([$Sheet1.$D$2:.$D$9])`
* Named ranges, when the document declares a `table:named-range` with that
  name.

Condition functions other than `cell-content-is-in-list` are reported as
`unverifiable`; they are not silently accepted.

## Relative references

`table:base-cell-address` is the anchor for relative references.  A reference
such as `.C1` is resolved against that anchor and the position of the cell
that carries `table:content-validation-name`.  This means one rule can be
shared by several rows while each row points at a different list:

```xml
<table:content-validation table:name="status"
    table:base-cell-address="$Sheet1.$B$2"
    table:condition="of:cell-content-is-in-list([$Sheet1.$C1:.$C1])"/>
```

With the anchor at `B2`, the rule for `B2` reads `C2`, the rule for `B3`
reads `C3`, and so on.  Absolute coordinates (with `$` before both the column
and the row, for example `$C$2`) do not move.

A rule without `table:base-cell-address` is anchored at the cell being
checked, so relative references move with that cell.

## Results

Every result has a status and, when relevant, the cell address, rule name,
checked value and a human-readable reason.

| Status | Meaning |
| --- | --- |
| `pass` | The value satisfies the rule. |
| `violation` | The value is a real value and does not satisfy the rule. |
| `unverifiable` | The rule cannot be evaluated, for example because the referenced range, sheet or rule is missing. `reason` says why. |

A blank submission is not the same as the number zero.  The caller passes
`None` (or omits the value and lets the stored cell content be read) for a
blank.  `table:allow-empty-cell` then decides whether the blank passes or is
a violation.  A submitted `0` is a value and is compared against the list.

## Example

```python
from odf.validation import check_submission

results = check_submission('orders.ods', {
    'Orders.B2': 'approved',
    'Orders.B3': None,
})
for result in results:
    if result.status != 'pass':
        print(result.cell_address, result.status, result.reason)
```

Lower-level entry points:

* `validate_cell(document, "Sheet.B2", value=...)` checks one value.
* `validate_values(document, pairs)` checks several submitted values.
* `validate_document(document)` checks the values currently stored in every
  bound cell.
* `evaluate_condition("of:cell-content-is-in-list(...)", value)` checks a
  condition without building a document.
