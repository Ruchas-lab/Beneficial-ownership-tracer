"""
Synthetic ownership structure generator.

Builds client ownership structures: scripted scenarios covering the patterns the
tool is meant to detect (layering, circular holdings, sanctions ownership cases,
trusts, nominee clusters and so on), plus randomly generated ordinary structures
that provide a realistic base of clean files.

Ground truth is written to a separate scenarios.csv. It is used only by the test
suite, never by the detection logic or the dashboard.
"""
import random

import numpy as np
import pandas as pd

random.seed(41)
np.random.seed(41)

NAME_A = ["Alder", "Meridian", "Corvus", "Lumen", "Vantor", "Nordhaven", "Aurelia", "Brightwell",
          "Kestrel", "Halcyon", "Stratos", "Pinecrest", "Oakmere", "Solstice", "Ardent", "Verity",
          "Tidewater", "Cobalt", "Ember", "Quorum"]
NAME_B = ["Capital", "Holdings", "Partners", "Industries", "Logistics", "Ventures", "Trading",
          "Investments", "Group", "Estates", "Technologies", "Advisory", "Resources", "Assets"]
SUFFIX = {"LU": "S.a r.l.", "DE": "GmbH", "FR": "SAS", "NL": "B.V.", "IE": "Ltd", "CH": "AG",
          "GB": "Ltd", "CY": "Ltd", "VG": "Ltd", "KY": "Ltd", "PA": "S.A.", "MT": "Ltd"}
FIRST = ["Alex", "Maria", "Jonas", "Priya", "Chen", "Sofia", "Omar", "Elena", "Lukas", "Amara",
         "Daniel", "Yuki", "Marco", "Ingrid", "Rahul", "Clara", "Hassan", "Nadia", "Tobias", "Lena"]
LAST = ["Weber", "Laurent", "Singh", "Rossi", "Khan", "Novak", "Martin", "Silva", "Meyer",
        "Okafor", "Hansen", "Costa", "Petrov", "Dubois", "Mehta", "Fischer", "Tanaka", "Kowalski"]
RES = ["DE", "FR", "IN", "GB", "US", "LU", "NL", "IT", "ES", "CH", "BR", "SG", "PL"]
STD_JUR = ["LU", "LU", "LU", "DE", "NL", "IE", "FR"]


class Builder:
    def __init__(self):
        self.nodes, self.edges, self.roles, self.scenarios = [], [], [], []
        self.n = 0
        self.s = 0
        self.cur = None
        self.client = 1000
        self.used = set()

    def new_structure(self):
        self.s += 1
        self.cur = f"S{self.s:03d}"
        return self.cur

    def _node(self, name, ntype, jur, **kw):
        self.n += 1
        nid = f"N{self.n:04d}"
        self.nodes.append({"node_id": nid, "name": name, "node_type": ntype, "jurisdiction": jur,
                           "address_id": kw.get("address"), "pep_flag": kw.get("pep", False),
                           "listed": kw.get("listed", False), "is_target": kw.get("target", False),
                           "client_id": kw.get("client_id"), "structure_id": kw.get("structure", self.cur)})
        return nid

    def person(self, name=None, pep=False, listed=False, structure=None):
        if name is None:
            while True:
                name = f"{random.choice(FIRST)} {random.choice(LAST)}"
                if name not in self.used:
                    self.used.add(name)
                    break
        jur = "--" if listed else random.choice(RES)
        return self._node(name, "INDIVIDUAL", jur, pep=pep, listed=listed,
                          structure=structure or self.cur)

    def listed(self, label):
        return self.person(name=f"Listed Person {label} (synthetic)", listed=True)

    def entity(self, kind="COMPANY", jur=None, target=False, address=None, name=None):
        jur = jur or random.choice(STD_JUR)
        if name is None:
            while True:
                name = f"{random.choice(NAME_A)} {random.choice(NAME_B)} {SUFFIX.get(jur, 'Ltd')}"
                if name not in self.used:
                    self.used.add(name)
                    break
        cid = None
        if target:
            self.client += 1
            cid = f"OWN-{self.client}"
        addr = address or f"AD-{self.n + 1:04d}"
        return self._node(name, kind, jur, address=addr, target=target, client_id=cid)

    def own(self, owner, owned, pct):
        self.edges.append({"owner_id": owner, "owned_id": owned, "pct": float(pct)})

    def role(self, person, entity, role):
        self.roles.append({"person_id": person, "entity_id": entity, "role": role})

    def tag(self, target, scenario):
        self.scenarios.append({"client_id": next(n["client_id"] for n in self.nodes if n["node_id"] == target),
                               "scenario": scenario})


def build():
    b = Builder()

    # --- Scripted scenarios --------------------------------------------------
    b.new_structure(); t = b.entity(target=True); h = b.entity(); p1, p2 = b.person(), b.person()
    b.own(h, t, 100); b.own(p1, h, 60); b.own(p2, h, 40); b.tag(t, "clean_simple")

    b.new_structure(); t = b.entity(target=True)
    h1, h2, h3, h4 = b.entity(jur="LU"), b.entity(jur="CY"), b.entity(jur="VG"), b.entity(jur="KY")
    p = b.person()
    b.own(h1, t, 100); b.own(h2, h1, 100); b.own(h3, h2, 100); b.own(h4, h3, 100); b.own(p, h4, 100)
    b.tag(t, "deep_layering")

    b.new_structure(); t = b.entity(target=True)
    x, y = b.entity(), b.entity()
    pa, pb, pc = b.person(), b.person(), b.person()
    b.own(x, t, 70); b.own(pa, t, 30); b.own(pb, x, 50); b.own(y, x, 50)
    b.own(x, y, 20); b.own(pc, y, 80); b.tag(t, "circular")

    b.new_structure(); t = b.entity(target=True); h = b.entity(); l = b.listed("1"); p = b.person()
    b.own(h, t, 100); b.own(l, h, 60); b.own(p, h, 40); b.tag(t, "listed_majority")

    b.new_structure(); t = b.entity(target=True); h = b.entity()
    l2, l3, p = b.listed("2"), b.listed("3"), b.person()
    b.own(h, t, 100); b.own(l2, h, 30); b.own(l3, h, 25); b.own(p, h, 45); b.tag(t, "listed_aggregate")

    b.new_structure(); t = b.entity(target=True); l = b.listed("4"); p = b.person()
    b.own(l, t, 49); b.own(p, t, 51); b.tag(t, "listed_below_threshold")

    b.new_structure(); t = b.entity(target=True); l = b.listed("5"); p = b.person()
    b.own(l, t, 50); b.own(p, t, 50); b.tag(t, "listed_exact_50")

    b.new_structure(); t = b.entity(target=True); i = b.entity(); l = b.listed("6"); p = b.person()
    b.own(i, t, 100); b.own(l, i, 40); b.own(p, i, 60); b.tag(t, "listed_diluted")

    b.new_structure(); t = b.entity(target=True); ha, hb = b.entity(), b.entity(); l = b.listed("7")
    b.own(ha, t, 100); b.own(hb, ha, 100); b.own(l, hb, 100); b.tag(t, "listed_chain")

    b.new_structure(); t = b.entity(target=True); tr = b.entity(kind="TRUST", jur="CH"); p = b.person()
    settlor, trustee = b.person(), b.person()
    b.own(tr, t, 70); b.own(p, t, 30); b.role(settlor, tr, "SETTLOR"); b.role(trustee, tr, "TRUSTEE")
    b.tag(t, "trust_with_control")

    b.new_structure(); t = b.entity(target=True); tr = b.entity(kind="TRUST", jur="CH")
    b.own(tr, t, 100); b.tag(t, "trust_no_control")

    b.new_structure(); t = b.entity(target=True); h = b.entity(); p1, p2 = b.person(), b.person()
    b.own(h, t, 100); b.own(p1, h, 40); b.own(p2, h, 15); b.tag(t, "untraced")

    b.new_structure(); t = b.entity(target=True); p1 = b.person(pep=True); p2 = b.person()
    b.own(p1, t, 60); b.own(p2, t, 40); b.tag(t, "pep")

    b.new_structure(); t = b.entity(target=True); lc = b.entity(kind="LISTED_COMPANY", jur="DE"); p = b.person()
    b.own(lc, t, 80); b.own(p, t, 20); b.tag(t, "listed_company")

    b.new_structure(); t = b.entity(target=True)
    for _ in range(5):
        b.own(b.person(), t, 20)
    b.tag(t, "fragmented")

    b.new_structure(); t = b.entity(target=True); p1, p2 = b.person(), b.person(); ld = b.listed("8")
    b.own(p1, t, 50); b.own(p2, t, 50); b.role(ld, t, "DIRECTOR"); b.tag(t, "listed_director")

    # Nominee and mass-address cluster: eight unrelated structures
    nominee = b.person(name="Cornelis Brandt", structure="SHARED")
    for _ in range(8):
        b.new_structure()
        t = b.entity(target=True)
        shell = b.entity(address="AD-VIRTUAL-01")
        owner = b.person()
        b.own(shell, t, 100); b.own(owner, shell, 100)
        b.role(nominee, t, "DIRECTOR"); b.role(nominee, shell, "DIRECTOR")
        b.tag(t, "nominee_cluster")

    # --- Ordinary structures -------------------------------------------------
    for _ in range(20):
        b.new_structure()
        t = b.entity(target=True, kind=random.choice(["COMPANY", "COMPANY", "FUND"]))
        depth = random.choice([1, 1, 2, 2, 3])
        owners_of = t
        for _ in range(depth - 1):
            mid = b.entity()
            b.own(mid, owners_of, 100)
            owners_of = mid
        k = random.choice([1, 2, 2, 3])
        first = round(random.uniform(40, 80), 1) if k > 1 else 100.0
        shares = [first]
        if k > 1:
            rest = np.random.dirichlet(np.ones(k - 1)) * (100 - first)
            shares += [round(float(r), 1) for r in rest]
            shares[-1] = round(100 - sum(shares[:-1]), 1)
        for s in shares:
            b.own(b.person(pep=random.random() < 0.08), owners_of, s)
        b.tag(t, "ordinary")

    nodes = pd.DataFrame(b.nodes)
    edges = pd.DataFrame(b.edges)
    roles = pd.DataFrame(b.roles)
    scen = pd.DataFrame(b.scenarios)
    return nodes, edges, roles, scen


if __name__ == "__main__":
    nodes, edges, roles, scen = build()
    nodes.to_csv("data/nodes.csv", index=False)
    edges.to_csv("data/edges.csv", index=False)
    roles.to_csv("data/roles.csv", index=False)
    scen.to_csv("data/scenarios.csv", index=False)
    print(f"Generated {nodes.is_target.sum()} client structures, {len(nodes)} nodes, "
          f"{len(edges)} ownership links, {len(roles)} role records")
