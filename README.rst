ofxparse
========

ofxparse is a parser for Open Financial Exchange (.ofx) format files.  OFX
files are available from almost any online banking site, so they work well
if you want to pull together your finances from multiple sources.  Online
trading accounts also provide account statements in OFX files.

There are three different types of OFX files, called BankAccount,
CreditAccount and InvestmentAccount files.  This library has been tested with
real-world samples of all three types.  If you find a file that does not work
with this library, please consider contributing the file so ofxparse can be
improved.  See the Help! section below for directions on how to do this.

Example Usage
=============

Here's a sample program

.. code:: python

  from ofxparse import OfxParser
  with codecs.open('file.ofx') as fileobj:
      ofx = OfxParser.parse(fileobj)
  
  # The OFX object
  
  ofx.account               # An Account object

  # AccountType
  # (Unknown, Bank, CreditCard, Investment)

  # Account
  
  account = ofx.account 
  account.account_id        # The account number
  account.number            # The account number (deprecated -- returns account_id)
  account.routing_number    # The bank routing number
  account.branch_id         # Transit ID / branch number
  account.type              # An AccountType object
  account.statement         # A Statement object
  account.institution       # An Institution object

  # InvestmentAccount(Account)

  account.brokerid          # Investment broker ID
  account.statement         # An InvestmentStatement object

  # Institution
  
  institution = account.institution
  institution.organization
  institution.fid
    
  # Statement
  
  statement = account.statement
  statement.start_date          # The start date of the transactions
  statement.end_date            # The end date of the transactions
  statement.balance             # The money in the account as of the statement date
  statement.available_balance   # The money available from the account as of the statement date
  statement.transactions        # A list of Transaction objects

  # InvestmentStatement

  statement = account.statement  
  statement.positions           # A list of Position objects
  statement.transactions        # A list of InvestmentTransaction objects

  # Transaction
  
  for transaction in statement.transactions:
    transaction.payee
    transaction.type
    transaction.date
    transaction.user_date
    transaction.amount
    transaction.id
    transaction.memo
    transaction.sic
    transaction.mcc
    transaction.checknum

  # InvestmentTransaction
  
  for transaction in statement.transactions:
    transaction.type
    transaction.tradeDate
    transaction.settleDate
    transaction.memo
    transaction.security      # A Security object
    transaction.income_type
    transaction.units
    transaction.unit_price
    transaction.comission
    transaction.fees
    transaction.total
    transaction.tferaction

  # Positions
  
  for position in statement.positions:
    position.security       # A Security object
    position.units
    position.unit_price
    position.market_value

  # Security

  security = transaction.security
  # or
  security = position.security
  security.uniqueid
  security.name
  security.ticker
  security.memo

Matching transfers across accounts
==================================

A security transfer is normally reported twice: an outgoing record in the
delivering account and an incoming record in the receiving account.  The two
FITIDs are usually different, so the records have to be matched on security,
direction, units and date.

.. code:: python

  from ofxparse import OfxParser, match_transfers

  ofx_a = OfxParser.parse(open('account_a.ofx', 'rb'))
  ofx_b = OfxParser.parse(open('account_b.ofx', 'rb'))

  result = match_transfers([ofx_a, ofx_b], settlement_window='3d')

  for pair in result.pairs:
      pair.outgoing.account_id, pair.outgoing.fitid
      pair.incoming.account_id, pair.incoming.fitid
      pair.min_quantity, pair.max_quantity   # equal when unambiguous

  for item in result.unmatched:
      item.record.account_id, item.record.security
      item.record.direction
      item.remaining_quantity                 # units still looking for a home
      item.reason

``match_transfers`` accepts one OFX result, an account, a statement, or any
iterable of those.  It can also be called as ``ofx.match_transfers()``.  The
``settlement_window`` is the largest allowed difference between the two
records' settlement dates (trade dates are used when one side lacks a
settlement date); it takes a ``timedelta``, a number of days, or strings such
as ``"3d"`` or ``"P3D"``.  Pass ``None`` to disable the date check.  Records
without any usable date are left unmatched when the window is enforced, with
``reason == "missing_date"``.

Units are treated as capacities, so one outgoing record may be split across
several incoming records, and several outgoing records may be allocated to one
incoming record.  Each record's units are used at most once.  The allocation is
solved as a maximum flow; when several allocations transfer the same maximum
number of units, only relationships that occur in *every* such allocation are
reported in ``result.pairs``.  Each pair then carries ``min_quantity`` and
``max_quantity``; a pair whose range still starts at zero is listed in
``result.ambiguities`` instead of being presented as a confirmed relationship.

For each record, ``remaining_quantity`` is the quantity that is still
unmatched (quantity minus the largest amount any valid allocation assigns to
it), and ``definitely_unmatched_quantity`` is the amount that is unmatched
in *every* valid allocation.  ``result.remaining_for(record)`` returns the
former, ``result.total_unmatched_outgoing`` / ``result.total_unmatched_incoming``
derive the totals from the maximum flow (so they do not depend on the chosen
allocation), and ``result.render()`` prints a short report.

The command line helper prints the same information::

  python utils/ofxmatch.py account_a.ofx account_b.ofx --window 3d

Help!
=====

Sample ``.ofx`` and ``.qfx`` files are very useful.  If you want to help us out,
please edit all identifying information from the file and then email it to
jseutter dot ofxparse at gmail dot com.

Development
===========

Prerequisites::
  # Ubuntu
  sudo apt-get install python-beautifulsoup python-nose python-coverage-test-runner
  # Python 3 (pip)
  pip install BeautifulSoup4 six lxml nose coverage
  # Python 2 (pip)
  pip install BeautifulSoup six nose coverage

The `six` package is required for python 2.X compatibility

Tests:
Simply running the ``nosetests`` command should run the tests.

.. code:: bash

  nosetests

If you don't have nose installed, the following might also work:

.. code:: bash

  python -m unittest tests.test_parse

Test Coverage Report:

.. code:: bash

  coverage run -m unittest tests.test_parse
  
  # text report
  coverage report

  # html report
  coverage html
  firefox htmlcov/index.html


Homepage
========
| Homepage: https://sites.google.com/site/ofxparse
| Source: https://github.com/jseutter/ofxparse

License
=======

ofxparse is released under an MIT license.  See the LICENSE file for the actual
license text.  The basic idea is that if you can use Python to do what you are
doing, you can also use this library.

