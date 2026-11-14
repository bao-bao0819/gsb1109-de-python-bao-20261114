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


Tax Lot Cost Ranges
===================

Old broker statements record buy and sell trades without saying which
tax lot each sold share came from.  Rather than assume an accounting
rule such as FIFO, ``ofxparse.costlots`` treats every lot assignment
consistent with the known data as feasible and reports the bounds of
that feasible set.

A buy lot can only fund a sale on or after its trade date, the same lot
share can never be consumed by two sales, and an end-of-day snapshot of
the per-lot remaining quantity excludes assignments that were feasible
from the trades alone.  For every sale the analysis returns the lowest
and highest possible cost and the lot shares forced into that sale
across *every* feasible allocation.  When nothing is forced, the result
says so instead of guessing.

.. code:: python

  from datetime import datetime
  from decimal import Decimal
  from ofxparse import (OfxParser, Snapshot, analyze_statement,
                        format_report)

  with codecs.open('trades.ofx') as fileobj:
      ofx = OfxParser.parse(fileobj)

  # Optional: end-of-day per-lot remaining quantities, keyed by the buy
  # transactions' FITIDs.  A later snapshot can rule out assignments that
  # looked feasible from the trades alone.
  snapshots = [
      Snapshot(datetime(2026, 2, 20), {'buy-1': Decimal(70),
                                       'buy-2': Decimal(70)}),
  ]

  reports = analyze_statement(ofx.account.statement, snapshots=snapshots)
  for security, report in reports.items():
      for result in report.ranges:
          result.sale.id            # the sell transaction FITID
          result.min_cost           # cheapest defensible cost
          result.max_cost           # priciest defensible cost
          result.cost_is_exact      # True when the bounds coincide
          result.forced_allocations # lot shares every allocation must use

  print(format_report(reports))

The same engine works on data assembled without an OFX file through
``analyze(lots, sales, snapshots)`` with ``Lot`` and ``Sale`` objects.
Lot unit cost defaults to the actual cash outlay per share
(``total / units``, including commission); pass ``basis='price'`` to use
the reported unit price instead.  If the trades and snapshots cannot all
hold at once, ``InfeasibleAllocationError`` is raised.


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

