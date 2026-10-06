# Beneficial Ownership and Sanctions Exposure Tracer

A tool that resolves who ultimately owns a client through layered holding structures, tests
whether sanctioned parties own it, and flags structural red flags that only show up when you
look at ownership as a network.

It addresses the hardest part of corporate KYC: a client file says "owned by Holdco", Holdco
is owned by two other companies, one of them offshore, and somewhere above that are the
people the bank actually needs to identify.

---

## Why this exists

Document checks are the easy half of KYC. The harder half is answering three questions about
a structure:

1. Who are the natural persons behind it, and how much do they really own?
2. Does any sanctioned party own or control it, directly or through intermediaries?
3. Does the structure itself look designed to obscure ownership?

This project answers all three, and shows its working so an analyst can check every number.

---

## What it does

### 1. Effective ownership through any number of layers

Effective ownership is the sum over every path of the product of the percentages along it.
A 60% holder of a company that owns 50% of the client holds an effective 30%.

Circular holdings break simple path counting, because ownership can loop through
cross-holdings indefinitely. The tool builds an ownership matrix `A` and computes integrated
ownership:

```
L = (I - A)^-1 = I + A + A^2 + A^3 + ...
```

`L[i, j]` is the total ownership of `j` attributable to `i` across every walk, loops
included. For a closed circular structure, the effective stakes of all ultimate holders
still sum to exactly 100%.

Worked check (also in the test suite): P owns 50% of X, Q owns 50% of Y, and X and Y each own
50% of the other, with X owning the client outright. Solving the loop by hand gives P 66.67%
and Q 33.33%. The engine returns the same.

### 2. UBO identification

- A natural person above the threshold (default 25%, configurable) is a UBO
- Trusts are not looked through by percentage. They are reported as untraced until settlor,
  trustee, protector and beneficiaries are identified
- A regulated listed company holder is treated as exempt from look-through
- Shares that no recorded owner accounts for are reported as unallocated
- If nobody exceeds the threshold, the file is flagged

### 3. Sanctions ownership test with propagation

Blocked status is propagated through ownership. An entity becomes blocked when the direct
stakes held in it by already-blocked parties reach the threshold, and the process repeats
until nothing changes. Stakes held through an intermediary that is not itself blocked do not
count.

Three conventions are provided, because the same structure can be blocked under one and
clear under another:

| Convention | Threshold | Holdings of several blocked parties |
|------------|-----------|-------------------------------------|
| OFAC-style | 50% or more | Added together |
| Majority test | More than 50% | Added together |
| Majority test | More than 50% | Tested separately |

The first follows the structure of the US OFAC 50 Percent Rule. The others model a stricter
"more than 50%" test with and without aggregation. The Sanctions Exposure view lists every
structure whose outcome depends on the convention.

Where the ownership test is not met but blocked parties hold a material effective interest
(25% or more), the structure is flagged for a control and influence assessment.

### 4. Structural and network red flags

| Flag | Logic |
|------|-------|
| Complex layering | 3+ intermediate entities between client and owners |
| Higher-risk jurisdiction | Any link in the chain in a listed jurisdiction |
| Layering through higher-risk jurisdictions | Both of the above, raised as high severity |
| Circular ownership | Cross-holdings detected in the chain |
| Shared nominee | One person in a director or nominee role at 5+ entities across 3+ unrelated structures |
| Mass registration address | One address shared by 8+ entities across 3+ unrelated structures |
| PEP beneficial owner | 10% or more effective ownership |
| Listed person in control role | A listed person as director, which ownership tests do not capture |

The last two network flags are the ones a document-by-document review cannot see.

---

## Design notes

**Matrix method, not path counting.** Enumerating paths is intuitive but wrong for circular
holdings. The matrix result is the one used for decisions. Simple paths are still shown next
to it for explainability, and the gap between the two is reported as the cross-holding effect.

**Top-most blocked parties.** Blocked-party interest is measured using only blocked parties
that have no blocked owner above them, so nested blocked holdings are not double counted.

**Ground truth kept separate.** Scripted test scenarios are recorded in `scenarios.csv`, which
is read only by the test suite. The detection logic and the dashboard never see it, so the
tests check detection rather than echo the answer key.

**A bug the tests caught.** The first version labelled a listed person owning 49% as "blocked
entity in chain", because any listed ancestor counted as a blocked node. Two scenario tests
failed. The fix measures exposure as the effective interest of the top-most blocked parties,
and splits the finding into a high-severity significant-interest case and a lower-severity
minor-interest case.

---

## Running it

```bash
pip install -r requirements.txt
python generate_data.py      # creates the synthetic portfolio
python test_engine.py        # runs the test suite
streamlit run app.py
```

---

## Tests

`test_engine.py` has 35 checks in three parts:

- **Mathematics:** hand-calculated chains, parallel paths, a circular structure, and sanctions
  propagation including aggregation and the exact-50% boundary
- **Scenarios:** every scripted pattern in the portfolio is detected, clean structures raise no
  findings, and for every structure the identified holders plus unallocated ownership equal
  exactly 100%
- **Regime sensitivity:** the aggregation case and the exact-50% case produce the expected
  differences across the three conventions

---

## Data

The dataset is synthetic: 44 client structures, 165 persons and entities, 118 ownership links
and 19 role records. Sixteen scripted single-structure scenarios cover the patterns above, plus an eight-structure
nominee and shared-address cluster. Twenty ordinary structures provide a base of clean files.
Listed persons are fictional and labelled as synthetic.

No real data is used.

---

## Project structure

```
beneficial-ownership-tracer/
├── app.py                 # Streamlit dashboard (5 views)
├── ownership_engine.py    # Effective ownership, UBOs, sanctions test, findings
├── generate_data.py       # Synthetic structures and scripted scenarios
├── test_engine.py         # Test suite
├── data/
│   ├── nodes.csv
│   ├── edges.csv
│   ├── roles.csv
│   └── scenarios.csv      # Test ground truth only
└── requirements.txt
```

---

## Limitations

This is a demonstration, not production software. The sanctions test covers ownership only.
Real regimes also apply control and influence tests, treat aggregation differently, and change
over time, so the thresholds here are illustrative and should be checked against current
guidance.

Ownership is modelled as share percentages. Voting rights, share classes, nominee
arrangements, trust beneficiary shares and information that is simply missing are outside the
model. A deployed system would also need registry integration, name matching against live
sanctions lists, and an audit trail for every analyst decision.
