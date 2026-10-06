"""
Beneficial Ownership and Sanctions Exposure Engine

Resolves effective ownership through multi-layer holding structures (including
circular holdings), identifies ultimate beneficial owners, applies a configurable
sanctions ownership test with propagation through intermediaries, and flags
structural red flags: layering, higher-risk jurisdictions, trusts, nominee
directors and shared registration addresses.
"""
import itertools
from dataclasses import dataclass

import networkx as nx
import numpy as np
import pandas as pd

HIGH_RISK_JURISDICTIONS = {"VG", "KY", "PA", "SC", "BZ"}

# Finding types
F_BLOCKED = "Blocked by ownership"
F_CHAIN_BLOCKED = "Blocked party in ownership chain"
F_ADJACENT = "Significant blocked-party interest"
F_LISTED_ROLE = "Listed person in control role"
F_NO_UBO = "No UBO above threshold"
F_UNTRACED = "Ownership not traced to natural persons"
F_TRUST = "Trust in ownership chain"
F_CYCLE = "Circular ownership"
F_COMPLEX_HR = "Complex structure via higher-risk jurisdictions"
F_COMPLEX = "Complex layering"
F_HR_JUR = "Higher-risk jurisdiction in chain"
F_PEP = "PEP beneficial owner"
F_NOMINEE = "Shared nominee or director"
F_MASS_ADDR = "Mass registration address"
F_LISTED_CO = "Listed company holder"

SEVERITY_WEIGHT = {"HIGH": 10, "MEDIUM": 4, "LOW": 1}
SEVERITY_ORDER = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}

# Sanctions ownership test presets. Regimes differ on the threshold, on whether
# holdings of several listed persons are aggregated, and on control tests. Only the
# ownership test is modelled here.
REGIMES = {
    "OFAC-style: 50% or more, aggregated": {"inclusive": True, "aggregate": True},
    "Majority test: more than 50%, aggregated": {"inclusive": False, "aggregate": True},
    "Majority test: more than 50%, single owner": {"inclusive": False, "aggregate": False},
}


@dataclass
class Params:
    block_threshold: float = 50.0
    inclusive: bool = True
    aggregate: bool = True
    ubo_threshold: float = 25.0
    adjacency_threshold: float = 25.0
    complex_layers: int = 3
    untraced_max: float = 25.0
    pep_min_stake: float = 10.0
    nominee_min_entities: int = 5
    nominee_min_structures: int = 3
    mass_address_min: int = 8


class OwnershipModel:
    """Ownership graph with an integrated (look-through) ownership matrix."""

    def __init__(self, nodes, edges, roles):
        self.nodes = nodes.set_index("node_id")
        self.edges = edges
        self.roles = roles
        self.ids = list(nodes.node_id)
        self.ix = {n: i for i, n in enumerate(self.ids)}

        n = len(self.ids)
        self.A = np.zeros((n, n))
        self.G = nx.DiGraph()
        self.G.add_nodes_from(self.ids)
        for e in edges.itertuples(index=False):
            self.A[self.ix[e.owner_id], self.ix[e.owned_id]] += e.pct / 100.0
            self.G.add_edge(e.owner_id, e.owned_id, pct=float(e.pct))

        self.L, self.closed_loop = self._integrated()

    def _integrated(self):
        """
        Integrated ownership L = (I - A)^-1 = I + A + A^2 + ...
        L[i, j] is the total ownership of j attributable to i, summed over every
        walk from i to j. Walks that loop through cross-holdings are included, which
        is what makes the result correct for circular structures.
        """
        n = len(self.ids)
        I = np.eye(n)
        if n == 0:
            return I, False
        rho = float(np.max(np.abs(np.linalg.eigvals(self.A))))
        if rho < 0.999999:
            return np.linalg.inv(I - self.A), False
        # Closed loop with no leakage: matrix is singular, use a truncated series
        L, P = I.copy(), I.copy()
        for _ in range(300):
            P = P @ self.A
            L += P
        return L, True

    def name(self, node_id):
        return self.nodes.loc[node_id, "name"]

    def validate(self):
        """Direct ownership of any entity must not exceed 100%."""
        totals = self.edges.groupby("owned_id").pct.sum()
        bad = totals[totals > 100.0001]
        if len(bad):
            raise ValueError(f"Ownership exceeds 100% for: {list(bad.index)}")

    # ------------------------------------------------------------------
    def analyze(self, target_id, params=None):
        params = params or Params()
        t = self.ix[target_id]
        anc = nx.ancestors(self.G, target_id)
        terminals = [n for n in anc if self.G.in_degree(n) == 0]

        cat_of = {"INDIVIDUAL": "Natural person", "TRUST": "Trust (no look-through)",
                  "LISTED_COMPANY": "Regulated listed company"}
        rows = []
        for n in terminals:
            nd = self.nodes.loc[n]
            rows.append({
                "node_id": n, "name": nd["name"], "node_type": nd["node_type"],
                "jurisdiction": nd["jurisdiction"],
                "category": cat_of.get(nd["node_type"], "Entity with unknown owners"),
                "effective_pct": float(self.L[self.ix[n], t] * 100),
                "pep": bool(nd["pep_flag"]), "listed": bool(nd["listed"]),
            })
        cols = ["node_id", "name", "node_type", "jurisdiction", "category",
                "effective_pct", "pep", "listed"]
        holders = pd.DataFrame(rows, columns=cols)

        # Share of the target that no recorded owner accounts for
        nonterminal = [j for j in anc if self.G.in_degree(j) > 0] + [target_id]
        unallocated = 0.0
        for j in nonterminal:
            u = 1.0 - self.A[:, self.ix[j]].sum()
            if u > 1e-9:
                unallocated += self.L[self.ix[j], t] * u
        unallocated *= 100

        # Path-level explanation (simple paths only)
        path_rows = []
        for h in terminals:
            for p in nx.all_simple_paths(self.G, h, target_id, cutoff=12):
                pct = 100.0
                for u, v in zip(p[:-1], p[1:]):
                    pct *= self.G[u][v]["pct"] / 100.0
                path_rows.append({
                    "holder_id": h, "holder": self.name(h),
                    "path": " > ".join(self.name(x) for x in p),
                    "entities_between": len(p) - 2, "contribution_pct": pct,
                })
        paths = pd.DataFrame(path_rows, columns=["holder_id", "holder", "path",
                                                 "entities_between", "contribution_pct"])
        if not holders.empty:
            psum = paths.groupby("holder_id").contribution_pct.sum() if not paths.empty else pd.Series(dtype=float)
            holders["path_sum_pct"] = holders.node_id.map(psum).fillna(0.0)
            holders["cycle_effect_pct"] = holders.effective_pct - holders.path_sum_pct
        else:
            holders["path_sum_pct"] = []
            holders["cycle_effect_pct"] = []

        layers = int(paths.entities_between.max()) if not paths.empty else 0

        sub = self.G.subgraph(anc | {target_id})
        cycles = list(itertools.islice(nx.simple_cycles(sub), 20))

        chain_nodes = anc | {target_id}
        hr = sorted({self.nodes.loc[n, "jurisdiction"] for n in chain_nodes} & HIGH_RISK_JURISDICTIONS)

        nat = holders[holders.category == "Natural person"]
        ubos = nat[nat.effective_pct > params.ubo_threshold + 1e-9].sort_values(
            "effective_pct", ascending=False)

        trust_pct = holders[holders.category == "Trust (no look-through)"].effective_pct.sum()
        other_pct = holders[holders.category == "Entity with unknown owners"].effective_pct.sum()
        exempt_pct = holders[holders.category == "Regulated listed company"].effective_pct.sum()

        return {
            "target": target_id, "ancestors": anc, "terminals": terminals,
            "holders": holders.sort_values("effective_pct", ascending=False).reset_index(drop=True),
            "paths": paths, "layers": layers, "cycles": cycles,
            "hr_jurisdictions": hr, "ubos": ubos,
            "natural_pct": float(nat.effective_pct.sum()) if not nat.empty else 0.0,
            "trust_pct": float(trust_pct), "other_pct": float(other_pct),
            "exempt_pct": float(exempt_pct), "unallocated_pct": float(unallocated),
            "untraced_pct": float(trust_pct + other_pct + unallocated),
            "n_entities": len(anc) + 1,
        }


# ----------------------------------------------------------------------
# SANCTIONS OWNERSHIP TEST
# ----------------------------------------------------------------------

def sanctions_blocked(model, threshold=50.0, inclusive=True, aggregate=True):
    """
    Propagate blocked status through ownership. A node becomes blocked when the
    direct stakes held in it by already-blocked nodes reach the threshold. Stakes
    held through an intermediary that is not itself blocked do not count, which is
    the behaviour of the OFAC 50 Percent Rule.
    Returns {node_id: {"basis", "pct", "owners"}}.
    """
    blocked = {n: {"basis": "Listed", "pct": None, "owners": []}
               for n in model.nodes.index[model.nodes.listed.astype(bool)]}
    changed = True
    while changed:
        changed = False
        for j in model.ids:
            if j in blocked:
                continue
            owners = [(i, d["pct"]) for i, _, d in model.G.in_edges(j, data=True) if i in blocked]
            if not owners:
                continue
            val = sum(p for _, p in owners) if aggregate else max(p for _, p in owners)
            ok = val >= threshold - 1e-9 if inclusive else val > threshold + 1e-9
            if ok:
                blocked[j] = {"basis": "Owned", "pct": val, "owners": owners}
                changed = True
    return blocked


def blocked_exposure(model, ancestors, target_id, blocked):
    """
    Effective interest in the client held by blocked parties. Counts only the
    top-most blocked parties (those with no blocked owner above them) so that
    nested blocked holdings are not double counted.
    """
    t = model.ix[target_id]
    top = [n for n in ancestors if n in blocked
           and not any(o in blocked for o in model.G.predecessors(n))]
    pct = sum(model.L[model.ix[n], t] for n in top) * 100
    return min(100.0, float(pct)), sorted(top)


# ----------------------------------------------------------------------
# NETWORK RED FLAGS
# ----------------------------------------------------------------------

def network_flags(model, params):
    """Nominee directors and registration addresses shared across unrelated structures."""
    nodes, roles = model.nodes, model.roles
    ctrl = roles[roles.role.isin(["DIRECTOR", "NOMINEE_SHAREHOLDER"])].copy()
    ctrl["structure_id"] = ctrl.entity_id.map(nodes.structure_id)

    nominees = pd.DataFrame(columns=["person_id", "name", "entities", "structures"])
    nominee_by_structure = {}
    if not ctrl.empty:
        per = ctrl.groupby("person_id").agg(
            entities=("entity_id", "nunique"), structures=("structure_id", "nunique")).reset_index()
        per["name"] = per.person_id.map(nodes["name"])
        nominees = per[(per.entities >= params.nominee_min_entities) &
                       (per.structures >= params.nominee_min_structures)]
        for r in nominees.itertuples():
            for s in ctrl[ctrl.person_id == r.person_id].structure_id.unique():
                nominee_by_structure.setdefault(s, []).append((r.name, int(r.entities), int(r.structures)))

    ents = nodes[nodes.node_type.isin(["COMPANY", "FUND", "TRUST", "LISTED_COMPANY"])
                 & nodes.address_id.notna()].reset_index()
    addr = ents.groupby("address_id").agg(
        entities=("node_id", "nunique"), structures=("structure_id", "nunique")).reset_index()
    mass = addr[(addr.entities >= params.mass_address_min) & (addr.structures >= params.nominee_min_structures)]
    address_by_structure = {}
    for r in mass.itertuples():
        for s in ents[ents.address_id == r.address_id].structure_id.unique():
            address_by_structure.setdefault(s, []).append((r.address_id, int(r.entities)))

    return {"nominees": nominees, "addresses": mass,
            "nominee_by_structure": nominee_by_structure,
            "address_by_structure": address_by_structure}


# ----------------------------------------------------------------------
# FINDINGS
# ----------------------------------------------------------------------

def build_findings(model, params=None):
    """Run every check on every client structure. Returns (findings, summary)."""
    params = params or Params()
    blocked = sanctions_blocked(model, params.block_threshold, params.inclusive, params.aggregate)
    net = network_flags(model, params)
    targets = model.nodes[model.nodes.is_target]

    findings, summary = [], []

    for tid, t in targets.iterrows():
        cid, sid, cname = t["client_id"], t["structure_id"], t["name"]
        a = model.analyze(tid, params)
        out = []

        def add(sev, ftype, cat, detail, action):
            out.append({"client_id": cid, "client_name": cname, "structure_id": sid,
                        "severity": sev, "category": cat, "finding_type": ftype,
                        "detail": detail, "action": action})

        # --- Sanctions ---
        exposure, top = blocked_exposure(model, a["ancestors"], tid, blocked)
        if tid in blocked:
            info = blocked[tid]
            if info["basis"] == "Listed":
                add("HIGH", F_BLOCKED, "Sanctions", f"{cname} is itself a listed party.",
                    "Escalate to Sanctions team and freeze relationship pending review.")
            else:
                who = ", ".join(f"{model.name(o)} ({p:g}%)" for o, p in info["owners"])
                add("HIGH", F_BLOCKED, "Sanctions",
                    f"{cname} meets the ownership test: {info['pct']:.1f}% held directly by blocked "
                    f"parties ({who}). Treat as blocked.",
                    "Escalate to Sanctions team and freeze relationship pending review.")
        elif top:
            names = ", ".join(model.name(n) for n in top)
            if exposure >= params.adjacency_threshold - 1e-9:
                add("HIGH", F_ADJACENT, "Sanctions",
                    f"Blocked parties ({names}) hold an effective {exposure:.1f}% of the client, below "
                    f"the {params.block_threshold:g}% blocking threshold. The ownership test is not met, "
                    f"but control, influence and related-party aggregation must be assessed.",
                    "Assess control and aggregation; apply enhanced due diligence.")
            else:
                add("MEDIUM", F_CHAIN_BLOCKED, "Sanctions",
                    f"Blocked parties ({names}) sit in the ownership chain with an effective interest "
                    f"of {exposure:.1f}%.",
                    "Assess control and confirm no dealings benefit the blocked party.")

        # Listed persons in control roles anywhere in the structure
        scope = a["ancestors"] | {tid}
        ctl = model.roles[model.roles.entity_id.isin(scope) &
                          model.roles.role.isin(["DIRECTOR", "NOMINEE_SHAREHOLDER"])]
        for r in ctl.itertuples():
            if bool(model.nodes.loc[r.person_id, "listed"]):
                add("HIGH", F_LISTED_ROLE, "Sanctions",
                    f"{model.name(r.person_id)} (listed) holds a {r.role.lower().replace('_', ' ')} role at "
                    f"{model.name(r.entity_id)}. Ownership tests do not capture control.",
                    "Apply control test; escalate to Sanctions team.")

        # --- Beneficial ownership ---
        holders = a["holders"]
        listed_co_big = holders[(holders.category == "Regulated listed company") &
                                (holders.effective_pct > params.ubo_threshold)]
        if a["ubos"].empty and listed_co_big.empty:
            add("HIGH", F_NO_UBO, "Beneficial ownership",
                f"No natural person holds more than {params.ubo_threshold:g}% effective ownership "
                f"(natural persons account for {a['natural_pct']:.1f}%).",
                "Trace further layers or document the senior managing official fallback.")
        if not listed_co_big.empty:
            r = listed_co_big.iloc[0]
            add("LOW", F_LISTED_CO, "Beneficial ownership",
                f"{r['name']} (regulated listed company) holds {r['effective_pct']:.1f}%. Look-through to "
                f"natural persons is not required under simplified treatment.",
                "Evidence the listing and record the basis for the exemption.")

        if a["untraced_pct"] > params.untraced_max + 1e-9:
            parts = []
            if a["trust_pct"] > 0.05:
                parts.append(f"{a['trust_pct']:.1f}% via trusts")
            if a["other_pct"] > 0.05:
                parts.append(f"{a['other_pct']:.1f}% via entities with unknown owners")
            if a["unallocated_pct"] > 0.05:
                parts.append(f"{a['unallocated_pct']:.1f}% unallocated")
            add("MEDIUM", F_UNTRACED, "Beneficial ownership",
                f"{a['untraced_pct']:.1f}% of the client is not traced to natural persons "
                f"({'; '.join(parts)}).",
                "Request shareholder registers and structure charts for the missing layers.")

        trusts = holders[holders.category == "Trust (no look-through)"]
        for r in trusts.itertuples():
            ctrl_people = model.roles[(model.roles.entity_id == r.node_id) &
                                      model.roles.role.isin(["SETTLOR", "TRUSTEE", "PROTECTOR", "BENEFICIARY"])]
            if ctrl_people.empty:
                add("HIGH", F_TRUST, "Beneficial ownership",
                    f"{r.name} holds an effective {r.effective_pct:.1f}% and no settlor, trustee, protector "
                    f"or beneficiary is recorded.",
                    "Obtain the trust deed and identify all control persons.")
            else:
                roles_txt = ", ".join(sorted(set(ctrl_people.role.str.lower())))
                add("MEDIUM", F_TRUST, "Beneficial ownership",
                    f"{r.name} holds an effective {r.effective_pct:.1f}%. Control persons recorded "
                    f"({roles_txt}) must each be identified and verified.",
                    "Verify settlor, trustee, protector and beneficiaries.")

        peps = holders[(holders.category == "Natural person") & holders.pep &
                       (holders.effective_pct >= params.pep_min_stake)]
        if not peps.empty:
            who = ", ".join(f"{r.name} ({r.effective_pct:.1f}%)" for r in peps.itertuples())
            add("MEDIUM", F_PEP, "Beneficial ownership",
                f"Politically exposed beneficial owner(s): {who}.",
                "Apply enhanced due diligence and senior management approval.")

        # --- Structure ---
        if a["cycles"]:
            c0 = " > ".join(model.name(n) for n in a["cycles"][0] + [a["cycles"][0][0]])
            add("MEDIUM", F_CYCLE, "Structure",
                f"{len(a['cycles'])} circular holding(s) detected, e.g. {c0}. Effective ownership is "
                f"computed with cross-holdings included.",
                "Obtain a structure chart and confirm the commercial rationale.")

        complex_ = a["layers"] >= params.complex_layers
        hr = a["hr_jurisdictions"]
        if complex_ and hr:
            add("HIGH", F_COMPLEX_HR, "Structure",
                f"{a['layers']} intermediate layers between the client and its owners, passing through "
                f"higher-risk jurisdictions ({', '.join(hr)}).",
                "Require rationale for the structure and enhanced due diligence.")
        else:
            if complex_:
                add("MEDIUM", F_COMPLEX, "Structure",
                    f"{a['layers']} intermediate layers between the client and its owners.",
                    "Document the commercial rationale for the layering.")
            if hr:
                add("MEDIUM", F_HR_JUR, "Structure",
                    f"Ownership chain includes higher-risk jurisdiction(s): {', '.join(hr)}.",
                    "Apply enhanced due diligence to the chain.")

        for nm, ne, ns in net["nominee_by_structure"].get(sid, []):
            add("MEDIUM", F_NOMINEE, "Network",
                f"{nm} holds a director or nominee role at {ne} entities across {ns} unrelated "
                f"structures.",
                "Establish whether the person acts as a nominee and who instructs them.")
        for ad, ne in net["address_by_structure"].get(sid, []):
            add("MEDIUM", F_MASS_ADDR, "Network",
                f"Registered address {ad} is shared by {ne} entities across unrelated structures.",
                "Confirm real substance and operations at the address.")

        findings += out
        score = sum(SEVERITY_WEIGHT[f["severity"]] for f in out)
        top = "HIGH" if any(f["severity"] == "HIGH" for f in out) else (
            "MEDIUM" if any(f["severity"] == "MEDIUM" for f in out) else (
                "LOW" if out else "CLEAR"))
        summary.append({
            "client_id": cid, "client_name": cname, "structure_id": sid,
            "entities": a["n_entities"], "layers": a["layers"],
            "ubos_identified": len(a["ubos"]), "natural_pct": a["natural_pct"],
            "untraced_pct": a["untraced_pct"], "cycles": len(a["cycles"]),
            "blocked_exposure_pct": exposure, "blocked": tid in blocked,
            "findings": len(out), "score": score, "rating": top,
        })

    cols = ["client_id", "client_name", "structure_id", "severity", "category",
            "finding_type", "detail", "action"]
    fdf = pd.DataFrame(findings, columns=cols)
    if not fdf.empty:
        fdf["_s"] = fdf.severity.map(SEVERITY_ORDER)
        fdf = fdf.sort_values(["_s", "client_id"]).drop(columns="_s").reset_index(drop=True)
        fdf.insert(0, "finding_id", [f"O{4000 + i}" for i in range(len(fdf))])
    else:
        fdf.insert(0, "finding_id", [])
    sdf = pd.DataFrame(summary).sort_values(["score", "client_id"], ascending=[False, True]).reset_index(drop=True)
    return fdf, sdf


# ----------------------------------------------------------------------
# VISUALISATION
# ----------------------------------------------------------------------

def to_dot(model, analysis, blocked):
    """Graphviz DOT for one client's ownership structure."""
    t = analysis["target"]
    nodes = analysis["ancestors"] | {t}
    ubo_ids = set(analysis["ubos"].node_id) if not analysis["ubos"].empty else set()
    eff = dict(zip(analysis["holders"].node_id, analysis["holders"].effective_pct))

    lines = ["digraph G {", "  rankdir=TB;", "  nodesep=0.5; ranksep=0.7;",
             '  node [fontname="Helvetica", fontsize=10, style=filled, margin="0.12,0.06"];',
             '  edge [fontname="Helvetica", fontsize=9, color="#7f8c8d"];']
    for n in sorted(nodes):
        nd = model.nodes.loc[n]
        shape = {"INDIVIDUAL": "ellipse", "TRUST": "hexagon"}.get(nd["node_type"], "box")
        fill = {"INDIVIDUAL": "#d6eaf8", "COMPANY": "#ecf0f1", "FUND": "#e8daef",
                "TRUST": "#fad7a0", "LISTED_COMPANY": "#d5f5e3"}.get(nd["node_type"], "#ecf0f1")
        color, pen = "#7f8c8d", 1
        tags = []
        if n in ubo_ids:
            fill, color, pen = "#f9e79f", "#b7950b", 2
            tags.append("UBO")
        if bool(nd["pep_flag"]):
            color, pen = "#e67e22", 2
            tags.append("PEP")
        if n in blocked:
            fill, color, pen = "#f5b7b1", "#c0392b", 3
            tags.append("BLOCKED")
        if n == t:
            pen = max(pen, 3)
            color = color if n in blocked else "#2c3e6b"
            fill = fill if n in blocked else "#aed6f1"
            tags.append("CLIENT")
        label = str(nd["name"]).replace('"', "'") + "\\n" + str(nd["jurisdiction"])
        if n in eff and n != t:
            label += f"\\n{eff[n]:.1f}% effective"
        if tags:
            label += "\\n[" + ", ".join(tags) + "]"
        lines.append(f'  "{n}" [label="{label}", shape={shape}, fillcolor="{fill}", color="{color}", penwidth={pen}];')
    for u, v, d in model.G.edges(data=True):
        if u in nodes and v in nodes:
            lines.append(f'  "{u}" -> "{v}" [label="{d["pct"]:g}%"];')
    lines.append("}")
    return "\n".join(lines)
