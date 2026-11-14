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

  # Posting date precision, offsets and business-day classification

  transaction.dtposted          # Structured OfxDate
  transaction.raw_date          # Exact DTPOSTED text from the bank
  transaction.date_only         # True for YYYYMMDD, false for a timestamp
  transaction.date_precision    # "date" or "datetime"
  transaction.fraction          # Fractional-second digits, exactly as reported
  transaction.reported_offset   # timedelta, or None when no offset was sent
  transaction.reported_offset_string  # e.g. "-05:00"
  transaction.timezone_name     # e.g. "EST"
  transaction.utc_datetime      # Only when both a time and an offset exist
  transaction.utc_sort_key      # Total UTC ordering key

  # Configure the local wall-clock switch time when parsing.
  ofx = OfxParser.parse(fileobj, business_day_cutoff="06:00")

  transaction.business_date              # One date, or None if ambiguous
  transaction.possible_business_dates    # All compatible business dates
  statement.sort_transactions_by_utc()

A date-only ``DTPOSTED`` has no time of day; it is exposed as
``transaction.date`` (a ``date``), never silently completed to midnight.
Its ``local_datetime``/``utc_datetime`` are ``None``.  When the business-day
switch time is not midnight, a date-only posting can belong to either the
named day or the next day, so ``business_date`` is ``None`` and
``possible_business_dates`` lists both candidates.  ``transaction.date`` for
a complete timestamp remains the historical naive-UTC ``datetime``.

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

