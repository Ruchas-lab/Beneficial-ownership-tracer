"""
Tests for the ownership engine. Run with:  python test_engine.py

Part 1 checks the ownership maths against hand-calculated results.
Part 2 checks that every scripted scenario in the synthetic portfolio is detected
(ground truth is read from data/scenarios.csv, which the engine never sees).
"""
import sys

import pandas as pd

from ownership_engine import (
    OwnershipModel, Params, REGIMES, build_findings, sanctions_blocked,
    F_BLOCKED, F_CHAIN_BLOCKED, F_ADJACENT, F_LISTED_ROLE, F_NO_UBO, F_UNTRACED, F_TRUST,
    F_CYCLE, F_COMPLEX_HR, F_PEP, F_NOMINEE, F_MASS_ADDR, F_LISTED_CO,
)

passed, failed = 0, []


def check(name, cond, detail=""):
    global passed
    if cond:
        passed += 1
        print(f"  PASS  {name}")
    else:
        failed.append(name)
        print(f"  FAIL  {name} {detail}")


def mini(nodes, edges, roles=None):
    """Build a small model. nodes: [(id, type, listed)], edges: [(owner, owned, pct)]."""
    n = pd.DataFrame([{"node_id": i, "name": i, "node_type": t, "jurisdiction": "LU",
                       "address_id": None, "pep_flag": False, "listed": l, "is_target": False,
                       "client_id": None, "structure_id": "S"} for i, t, l in nodes])
    e = pd.DataFrame([{"owner_id": a, "owned_id": b, "pct": p} for a, b, p in edges])
    r = pd.DataFrame(roles or [], columns=["person_id", "entity_id", "role"])
    return OwnershipModel(n, e, r)


def eff(model, holder, target):
    return model.L[model.ix[holder], model.ix[target]] * 100


print("Part 1: ownership mathematics")

m = mini([("P", "INDIVIDUAL", False), ("A", "COMPANY", False), ("T", "COMPANY", False)],
         [("P", "A", 60), ("A", "T", 50)])
check("chain multiplies: 60% x 50% = 30%", abs(eff(m, "P", "T") - 30.0) < 1e-9)

m = mini([("P", "INDIVIDUAL", False), ("A", "COMPANY", False), ("T", "COMPANY", False)],
         [("P", "A", 50), ("A", "T", 100), ("P", "T", 20)])
check("parallel paths add: 50% x 100% + 20% = 70% (T not 100% allocated)",
      abs(eff(m, "P", "T") - 70.0) < 1e-9)

# Circular holdings, hand-solved: P owns 50% of X, Q owns 50% of Y, X and Y each own 50% of the other
m = mini([("P", "INDIVIDUAL", False), ("Q", "INDIVIDUAL", False), ("X", "COMPANY", False),
          ("Y", "COMPANY", False), ("T", "COMPANY", False)],
         [("P", "X", 50), ("Q", "Y", 50), ("X", "Y", 50), ("Y", "X", 50), ("X", "T", 100)])
check("circular holdings: P holds 66.67% of X-owned target", abs(eff(m, "P", "T") - 200 / 3) < 1e-6)
check("circular holdings: Q holds 33.33%", abs(eff(m, "Q", "T") - 100 / 3) < 1e-6)
check("circular holdings: owners sum to 100%", abs(eff(m, "P", "T") + eff(m, "Q", "T") - 100) < 1e-6)

# Sanctions propagation
L = ("L", "INDIVIDUAL", True)
m = mini([L, ("H", "COMPANY", False), ("T", "COMPANY", False)], [("L", "H", 60), ("H", "T", 100)])
b = sanctions_blocked(m)
check("60% listed owner blocks intermediary and propagates to client", "H" in b and "T" in b)

m = mini([L, ("H", "COMPANY", False), ("T", "COMPANY", False)], [("L", "H", 40), ("H", "T", 100)])
check("40% listed owner does not block (diluted)", "H" not in sanctions_blocked(m))

m = mini([("L1", "INDIVIDUAL", True), ("L2", "INDIVIDUAL", True), ("T", "COMPANY", False)],
         [("L1", "T", 30), ("L2", "T", 25)])
check("aggregated 30% + 25% blocks", "T" in sanctions_blocked(m, 50, True, True))
check("same holdings not aggregated do not block", "T" not in sanctions_blocked(m, 50, True, False))

m = mini([L, ("T", "COMPANY", False)], [("L", "T", 50)])
check("exactly 50% blocks under 'or more'", "T" in sanctions_blocked(m, 50, True, True))
check("exactly 50% does not block under 'more than'", "T" not in sanctions_blocked(m, 50, False, True))

print("\nPart 2: scripted scenarios on the synthetic portfolio")

nodes = pd.read_csv("data/nodes.csv")
edges = pd.read_csv("data/edges.csv")
roles = pd.read_csv("data/roles.csv")
scen = pd.read_csv("data/scenarios.csv")
model = OwnershipModel(nodes, edges, roles)
model.validate()

findings, summary = build_findings(model, Params())
types = findings.groupby("client_id").finding_type.apply(set).to_dict()
sev = findings.groupby(["client_id", "finding_type"]).severity.first().to_dict()
cid = {s: scen[scen.scenario == s].client_id.tolist() for s in scen.scenario.unique()}


def has(scenario, ftype):
    return all(ftype in types.get(c, set()) for c in cid[scenario])


for c in nodes[nodes.is_target].itertuples():
    a = model.analyze(c.node_id)
    total = a["holders"].effective_pct.sum() + a["unallocated_pct"]
    if abs(total - 100) > 1e-6:
        failed.append(f"identity {c.client_id}")
        print(f"  FAIL  identity for {c.client_id}: {total:.6f}")
        break
else:
    passed += 1
    print(f"  PASS  holders + unallocated = 100% for all {nodes.is_target.sum()} structures")

check("clean structure raises no findings", all(c not in types for c in cid["clean_simple"]))
check("ordinary structures: no sanctions or structure HIGH findings",
      not findings[findings.client_id.isin(cid["ordinary"]) & (findings.severity == "HIGH")].shape[0])
check("deep layering via VG/KY flagged", has("deep_layering", F_COMPLEX_HR))
check("circular ownership flagged", has("circular", F_CYCLE))

a = model.analyze(nodes[nodes.client_id == cid["circular"][0]].node_id.iloc[0])
h = a["holders"].set_index("name")["effective_pct"]
check("circular scenario effective stakes sum with cross-holdings", abs(h.sum() - 100) < 1e-6)

check("majority listed owner: blocked", has("listed_majority", F_BLOCKED))
check("aggregate case: blocked under default regime", has("listed_aggregate", F_BLOCKED))
check("49% listed: adjacency flag, not blocked",
      has("listed_below_threshold", F_ADJACENT) and not has("listed_below_threshold", F_BLOCKED))
check("exactly 50% listed: blocked under default regime", has("listed_exact_50", F_BLOCKED))
check("diluted 40%: adjacency flag, not blocked",
      has("listed_diluted", F_ADJACENT) and not has("listed_diluted", F_BLOCKED))
check("listed parent chain: blocked", has("listed_chain", F_BLOCKED))
check("trust with recorded control persons: MEDIUM",
      all(sev[(c, F_TRUST)] == "MEDIUM" for c in cid["trust_with_control"]))
check("trust with no control persons: HIGH",
      all(sev[(c, F_TRUST)] == "HIGH" for c in cid["trust_no_control"]))
check("untraced ownership flagged", has("untraced", F_UNTRACED))
check("PEP beneficial owner flagged", has("pep", F_PEP))
check("listed-company holder handled, no false UBO gap",
      has("listed_company", F_LISTED_CO) and not has("listed_company", F_NO_UBO))
check("fragmented 5 x 20% flagged as no UBO", has("fragmented", F_NO_UBO))
check("listed director flagged", has("listed_director", F_LISTED_ROLE))
check("nominee director shared across structures flagged", has("nominee_cluster", F_NOMINEE))
check("mass registration address flagged", has("nominee_cluster", F_MASS_ADDR))

print("\nPart 3: regime sensitivity")
for label, cfg in REGIMES.items():
    f, _ = build_findings(model, Params(inclusive=cfg["inclusive"], aggregate=cfg["aggregate"]))
    blocked_clients = set(f[f.finding_type == F_BLOCKED].client_id)
    agg = all(c in blocked_clients for c in cid["listed_aggregate"])
    ex50 = all(c in blocked_clients for c in cid["listed_exact_50"])
    print(f"  {label:<46} aggregate case blocked: {agg!s:<5} exact-50 case blocked: {ex50}")
    if label.startswith("OFAC"):
        check("OFAC-style blocks both regime-sensitive cases", agg and ex50)
    elif label.endswith("aggregated"):
        check("majority test (aggregated) blocks aggregate case only", agg and not ex50)
    else:
        check("majority test (single owner) blocks neither", not agg and not ex50)

print(f"\n{passed} passed, {len(failed)} failed")
sys.exit(1 if failed else 0)
