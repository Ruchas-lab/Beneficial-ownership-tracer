"""
Beneficial Ownership and Sanctions Exposure Tracer
Streamlit dashboard for resolving ownership through layered structures,
identifying UBOs, testing sanctions exposure and surfacing structural red flags.
"""
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from ownership_engine import (
    OwnershipModel, Params, REGIMES, build_findings, sanctions_blocked, network_flags,
    to_dot, HIGH_RISK_JURISDICTIONS,
)

st.set_page_config(page_title="Ownership and Sanctions Tracer", layout="wide",
                   initial_sidebar_state="expanded")

ACCENT = "#2c3e6b"
RATING_COLORS = {"HIGH": "#c0392b", "MEDIUM": "#e67e22", "LOW": "#f1c40f", "CLEAR": "#27ae60"}
SEV_COLORS = {"HIGH": "#c0392b", "MEDIUM": "#e67e22", "LOW": "#f1c40f"}
RATING_ORDER = {"HIGH": 0, "MEDIUM": 1, "LOW": 2, "CLEAR": 3}

st.markdown("""
<style>
.main .block-container {padding-top: 2rem;}
h1 {color: #2c3e6b; font-size: 1.9rem;}
h2 {color: #2c3e6b; font-size: 1.3rem; margin-top: 1.2rem;}
.stMetric {background: #f8f9fa; padding: 0.8rem; border-radius: 6px;
           border-left: 3px solid #2c3e6b;}
</style>
""", unsafe_allow_html=True)


@st.cache_resource
def load_model():
    nodes = pd.read_csv("data/nodes.csv")
    edges = pd.read_csv("data/edges.csv")
    roles = pd.read_csv("data/roles.csv")
    model = OwnershipModel(nodes, edges, roles)
    model.validate()
    return model


@st.cache_data
def run_analysis(regime, ubo_thr, layers):
    model = load_model()
    cfg = REGIMES[regime]
    params = Params(inclusive=cfg["inclusive"], aggregate=cfg["aggregate"],
                    ubo_threshold=float(ubo_thr), complex_layers=int(layers))
    findings, summary = build_findings(model, params)
    return findings, summary


model = load_model()

# ===== SIDEBAR =====
st.sidebar.markdown("### Ownership and Sanctions Tracer")
st.sidebar.caption("Effective ownership through layered structures, UBO identification and sanctions ownership testing.")
st.sidebar.markdown("---")

page = st.sidebar.radio("View", [
    "Portfolio Overview",
    "Structure Explorer",
    "Sanctions Exposure",
    "Network Red Flags",
    "Method and Rules",
])

st.sidebar.markdown("---")
st.sidebar.markdown("**Configuration**")
regime = st.sidebar.selectbox("Sanctions ownership test", list(REGIMES.keys()))
ubo_thr = st.sidebar.slider("UBO threshold (% effective)", 10, 50, 25)
layers = st.sidebar.slider("Complex layering (intermediate entities)", 2, 6, 3)

cfg = REGIMES[regime]
params = Params(inclusive=cfg["inclusive"], aggregate=cfg["aggregate"],
                ubo_threshold=float(ubo_thr), complex_layers=int(layers))
findings, summary = run_analysis(regime, ubo_thr, layers)

st.sidebar.markdown("---")
st.sidebar.write(f"Client structures: {len(summary)}")
st.sidebar.write(f"Entities and persons: {len(model.ids)}")
st.sidebar.write(f"Ownership links: {len(model.edges)}")
st.sidebar.caption("Synthetic data. Built to demonstrate ownership tracing logic.")


# ===== PAGE 1: OVERVIEW =====
if page == "Portfolio Overview":
    st.title("Portfolio Overview")
    st.caption(f"{len(summary)} client structures · sanctions test: {regime}")

    c1, c2, c3, c4, c5, c6 = st.columns(6)
    c1.metric("Structures", len(summary))
    c2.metric("Clear", int((summary.rating == "CLEAR").sum()))
    c3.metric("High rated", int((summary.rating == "HIGH").sum()))
    c4.metric("Blocked", int(summary.blocked.sum()))
    c5.metric("No UBO found", int((summary.ubos_identified == 0).sum()))
    c6.metric("Circular", int((summary.cycles > 0).sum()))

    st.markdown("---")
    left, right = st.columns(2)

    with left:
        st.subheader("Structure rating")
        dist = summary.rating.value_counts().reindex(["HIGH", "MEDIUM", "LOW", "CLEAR"]).fillna(0)
        fig = go.Figure(go.Bar(x=dist.index, y=dist.values,
                               marker_color=[RATING_COLORS[r] for r in dist.index],
                               text=dist.values.astype(int), textposition="outside"))
        fig.update_layout(height=310, margin=dict(l=0, r=0, t=10, b=0),
                          yaxis_title="Structures", xaxis_title="")
        st.plotly_chart(fig, width="stretch")

    with right:
        st.subheader("Findings by type")
        if findings.empty:
            st.info("No findings.")
        else:
            bt = findings.groupby(["finding_type", "severity"]).size().reset_index(name="count")
            order = findings.finding_type.value_counts().index.tolist()[::-1]
            fig = px.bar(bt, x="count", y="finding_type", color="severity", orientation="h",
                         color_discrete_map=SEV_COLORS,
                         category_orders={"finding_type": order, "severity": ["HIGH", "MEDIUM", "LOW"]},
                         labels={"count": "Findings", "finding_type": "", "severity": "Severity"})
            fig.update_layout(height=310, margin=dict(l=0, r=0, t=10, b=0))
            st.plotly_chart(fig, width="stretch")

    st.markdown("---")
    st.subheader("Structures ranked by risk")
    view = summary[["client_id", "client_name", "rating", "score", "findings", "layers",
                    "ubos_identified", "natural_pct", "untraced_pct", "blocked_exposure_pct",
                    "blocked", "cycles"]].copy()
    view["_r"] = view.rating.map(RATING_ORDER)
    view = view.sort_values(["_r", "score"], ascending=[True, False]).drop(columns="_r")
    show_clear = st.checkbox("Include clear structures", value=False)
    if not show_clear:
        view = view[view.rating != "CLEAR"]
    st.dataframe(view, width="stretch", hide_index=True, column_config={
        "client_id": "Client ID", "client_name": "Name", "rating": "Rating",
        "score": "Score", "findings": "Findings", "layers": "Layers",
        "ubos_identified": "UBOs",
        "natural_pct": st.column_config.NumberColumn("Traced to people %", format="%.1f"),
        "untraced_pct": st.column_config.NumberColumn("Untraced %", format="%.1f"),
        "blocked_exposure_pct": st.column_config.NumberColumn("Blocked-party interest %", format="%.1f"),
        "blocked": "Blocked", "cycles": "Cycles"})


# ===== PAGE 2: STRUCTURE EXPLORER =====
elif page == "Structure Explorer":
    st.title("Structure Explorer")
    st.caption("Ownership diagram, effective ownership by holder and findings for one client.")

    ordered = summary.assign(_r=summary.rating.map(RATING_ORDER)).sort_values(
        ["_r", "score"], ascending=[True, False])
    options = [f"{r.client_id} | {r.client_name} | {r.rating}" for r in ordered.itertuples()]
    choice = st.selectbox("Client", options)
    cid = choice.split(" | ")[0]

    tid = model.nodes.index[model.nodes.client_id == cid][0]
    a = model.analyze(tid, params)
    blocked = sanctions_blocked(model, params.block_threshold, params.inclusive, params.aggregate)
    row = summary[summary.client_id == cid].iloc[0]

    st.markdown("---")
    m1, m2, m3, m4, m5, m6 = st.columns(6)
    m1.metric("Rating", row.rating)
    m2.metric("Entities in structure", a["n_entities"])
    m3.metric("Intermediate layers", a["layers"])
    m4.metric("UBOs identified", len(a["ubos"]))
    m5.metric("Traced to people", f"{a['natural_pct']:.1f}%")
    m6.metric("Untraced", f"{a['untraced_pct']:.1f}%")

    st.subheader("Ownership structure")
    st.graphviz_chart(to_dot(model, a, blocked), width="stretch")
    st.caption("Yellow: UBO. Red: blocked. Blue outline: client. Orange outline: PEP. "
               "Percentages on arrows are direct holdings; percentages in boxes are effective ownership.")

    st.markdown("---")
    st.subheader("Where the ownership goes")
    parts = [("Natural persons", a["natural_pct"], "#2c3e6b"),
             ("Regulated listed company", a["exempt_pct"], "#27ae60"),
             ("Trusts", a["trust_pct"], "#e67e22"),
             ("Entities with unknown owners", a["other_pct"], "#c0392b"),
             ("Unallocated", a["unallocated_pct"], "#95a5a6")]
    fig = go.Figure()
    for label, val, color in parts:
        fig.add_trace(go.Bar(y=[""], x=[val], name=label, orientation="h", marker_color=color,
                             text=[f"{val:.1f}%" if val > 3 else ""], textposition="inside"))
    fig.update_layout(barmode="stack", height=130, margin=dict(l=0, r=0, t=10, b=0),
                      xaxis=dict(range=[0, 100], title="% of client"), legend=dict(orientation="h", y=-0.6))
    st.plotly_chart(fig, width="stretch")

    st.subheader("Effective ownership by ultimate holder")
    h = a["holders"].copy()
    h["is_ubo"] = (h.category == "Natural person") & (h.effective_pct > ubo_thr + 1e-9)
    h["blocked"] = h.node_id.isin(blocked.keys())
    st.dataframe(h[["name", "category", "jurisdiction", "effective_pct", "path_sum_pct",
                    "cycle_effect_pct", "is_ubo", "pep", "blocked"]],
                 width="stretch", hide_index=True, column_config={
                     "name": "Holder", "category": "Category", "jurisdiction": "Jur.",
                     "effective_pct": st.column_config.NumberColumn("Effective %", format="%.2f"),
                     "path_sum_pct": st.column_config.NumberColumn("Sum of paths %", format="%.2f"),
                     "cycle_effect_pct": st.column_config.NumberColumn(
                         "Cross-holding effect %", format="%.2f",
                         help="Extra ownership that exists only because of circular holdings"),
                     "is_ubo": "UBO", "pep": "PEP", "blocked": "Blocked"})

    with st.expander("Ownership paths"):
        if a["paths"].empty:
            st.write("No ownership paths.")
        else:
            st.dataframe(a["paths"].sort_values("contribution_pct", ascending=False),
                         width="stretch", hide_index=True, column_config={
                             "holder_id": None, "holder": "Holder", "path": "Path (top to client)",
                             "entities_between": "Entities between",
                             "contribution_pct": st.column_config.NumberColumn("Contribution %", format="%.2f")})

    st.markdown("---")
    st.subheader("Findings")
    cf = findings[findings.client_id == cid]
    if cf.empty:
        st.success("No findings. Ownership is traced, and no sanctions or structural flags are raised.")
    else:
        for r in cf.itertuples():
            st.markdown(f"<span style='color:{SEV_COLORS[r.severity]};font-weight:600'>{r.severity}</span> · "
                        f"**{r.finding_type}** · {r.category}", unsafe_allow_html=True)
            st.write(r.detail)
            st.caption(f"Recommended action: {r.action}")


# ===== PAGE 3: SANCTIONS EXPOSURE =====
elif page == "Sanctions Exposure":
    st.title("Sanctions Exposure")
    st.caption("The ownership test applied under three conventions. Regimes differ on the threshold, "
               "on aggregation and on control tests, so the same structure can be blocked under one "
               "convention and clear under another.")

    targets = model.nodes[model.nodes.is_target]
    res = targets[["client_id", "name"]].rename(columns={"name": "client_name"}).copy()
    for label, c in REGIMES.items():
        b = sanctions_blocked(model, 50.0, c["inclusive"], c["aggregate"])
        res[label] = ["Blocked" if t in b else "Clear" for t in targets.index]
    labels = list(REGIMES.keys())
    res["regime_sensitive"] = res[labels].nunique(axis=1) > 1
    res = res.merge(summary[["client_id", "blocked_exposure_pct", "rating"]], on="client_id")

    k1, k2, k3 = st.columns(3)
    for col, label in zip((k1, k2, k3), labels):
        col.metric(label.split(":")[0] + (" (aggregated)" if "aggregated" in label else " (single owner)"
                                          if "single" in label else ""),
                   f"{int((res[label] == 'Blocked').sum())} blocked")

    st.markdown("---")
    st.subheader("Structures where the outcome depends on the regime")
    sens = res[res.regime_sensitive]
    if sens.empty:
        st.info("No regime-sensitive structures.")
    else:
        st.dataframe(sens[["client_id", "client_name"] + labels], width="stretch", hide_index=True)
        st.caption("These need an analyst decision on which regime applies to the client, "
                   "and a control assessment where the ownership test alone does not settle it.")

    st.markdown("---")
    st.subheader("Structures with blocked-party interest")
    exposed = res[(res.blocked_exposure_pct > 0)].sort_values("blocked_exposure_pct", ascending=False)
    if exposed.empty:
        st.info("No structure has a blocked party in its ownership chain.")
    else:
        st.dataframe(exposed[["client_id", "client_name", "blocked_exposure_pct", labels[0], "rating"]],
                     width="stretch", hide_index=True, column_config={
                         "client_id": "Client ID", "client_name": "Name",
                         "blocked_exposure_pct": st.column_config.NumberColumn(
                             "Effective blocked-party interest %", format="%.1f"),
                         labels[0]: "Status (selected default)", "rating": "Rating"})
        st.caption("Interest below the blocking threshold does not make a client blocked, "
                   "but it still needs a control and influence assessment.")


# ===== PAGE 4: NETWORK RED FLAGS =====
elif page == "Network Red Flags":
    st.title("Network Red Flags")
    st.caption("Patterns that only appear when structures are viewed together rather than one at a time.")

    net = network_flags(model, params)

    st.subheader("Directors and nominees spread across unrelated structures")
    st.write(f"Flagged when a person holds a director or nominee role at {params.nominee_min_entities}+ entities "
             f"across {params.nominee_min_structures}+ unrelated structures.")
    if net["nominees"].empty:
        st.info("None found.")
    else:
        st.dataframe(net["nominees"][["name", "entities", "structures"]], width="stretch", hide_index=True,
                     column_config={"name": "Person", "entities": "Entities", "structures": "Structures"})

    st.markdown("---")
    st.subheader("Mass registration addresses")
    st.write(f"Flagged when {params.mass_address_min}+ entities in {params.nominee_min_structures}+ unrelated "
             f"structures share one registered address.")
    if net["addresses"].empty:
        st.info("None found.")
    else:
        st.dataframe(net["addresses"], width="stretch", hide_index=True,
                     column_config={"address_id": "Address", "entities": "Entities", "structures": "Structures"})

    st.markdown("---")
    st.subheader("Circular ownership")
    circ = summary[summary.cycles > 0]
    if circ.empty:
        st.info("No circular holdings found.")
    else:
        st.dataframe(circ[["client_id", "client_name", "cycles", "layers"]], width="stretch",
                     hide_index=True, column_config={"client_id": "Client ID", "client_name": "Name",
                                                     "cycles": "Cycles", "layers": "Layers"})

    st.markdown("---")
    st.subheader("Layering and jurisdiction")
    lay = summary[summary.layers >= layers].sort_values("layers", ascending=False)
    if lay.empty:
        st.info("No structure meets the layering threshold.")
    else:
        st.dataframe(lay[["client_id", "client_name", "layers", "entities"]], width="stretch",
                     hide_index=True, column_config={"client_id": "Client ID", "client_name": "Name",
                                                     "layers": "Intermediate layers", "entities": "Entities"})
    st.caption(f"Higher-risk jurisdictions used in this model (illustrative): "
               f"{', '.join(sorted(HIGH_RISK_JURISDICTIONS))}.")


# ===== PAGE 5: METHOD AND RULES =====
elif page == "Method and Rules":
    st.title("Method and Rules")

    st.subheader("Effective ownership")
    st.write(
        "Effective ownership of a client by a person is the sum, over every ownership path, of the "
        "product of the percentages along the path. A 60% holder of a company that owns 50% of the "
        "client has an effective 30% interest."
    )
    st.write(
        "Circular holdings make path enumeration unreliable, because ownership can loop through "
        "cross-holdings indefinitely. The tool instead builds an ownership matrix A, where A[i, j] is "
        "the share of j held directly by i, and computes integrated ownership:"
    )
    st.latex(r"L = (I - A)^{-1} = I + A + A^2 + A^3 + \dots")
    st.write(
        "L[i, j] is the total ownership of j attributable to i across every walk, including loops. "
        "For a closed circular structure the effective stakes of all ultimate holders still add up to "
        "100%. The Explorer shows the plain path sum next to the matrix result, and the difference is "
        "reported as the cross-holding effect."
    )

    st.subheader("Beneficial ownership rules")
    st.write(
        f"- A natural person with more than {ubo_thr}% effective ownership is a UBO (configurable; the EU "
        f"AMLD definition uses 25% plus one share).\n"
        "- Trusts are not looked through by percentage. They are reported as untraced until the settlor, "
        "trustee, protector and beneficiaries are identified.\n"
        "- A regulated listed company holder is treated as exempt from look-through.\n"
        "- If no natural person exceeds the threshold, the file is flagged until further layers are traced or "
        "the senior managing official fallback is documented.\n"
        "- A client is flagged for untraced ownership when more than 25% is held through trusts, entities "
        "with unknown owners or unallocated shares."
    )

    st.subheader("Sanctions ownership test")
    st.write(
        "Blocked status is propagated through ownership. An entity becomes blocked when the direct stakes "
        "held in it by already-blocked parties reach the threshold. Stakes held through an intermediary "
        "that is not itself blocked do not count. The process repeats until no further entity changes "
        "status."
    )
    st.dataframe(pd.DataFrame([
        {"Convention": k, "Threshold": ">= 50%" if v["inclusive"] else "> 50%",
         "Holdings of several blocked parties": "Added together" if v["aggregate"] else "Tested separately"}
        for k, v in REGIMES.items()]), width="stretch", hide_index=True)
    st.write(
        "Where the ownership test is not met but blocked parties hold a material interest "
        "(25% or more effective), the structure is flagged for a control and influence assessment."
    )

    st.subheader("Structural red flags")
    st.write(
        f"- **Complex layering:** {layers}+ intermediate entities between the client and its owners.\n"
        "- **Higher-risk jurisdictions:** any link in the chain located in a jurisdiction on the "
        "illustrative list. Layering combined with these jurisdictions is raised as high severity.\n"
        "- **Circular ownership:** cross-holdings between entities in the chain.\n"
        "- **Shared nominees and mass addresses:** detected across structures, not within one.\n"
        "- **PEP beneficial owners:** 10% or more effective ownership."
    )

    st.subheader("Limitations")
    st.write(
        "The sanctions test covers ownership only. Real regimes also apply control and influence tests, "
        "differ in their treatment of aggregation, and change over time, so thresholds here are "
        "illustrative and should be checked against current guidance. Ownership is modelled as share "
        "percentages: voting rights, classes of shares, nominee arrangements, trust beneficiary shares "
        "and information that is simply missing are outside the model. All data is synthetic."
    )
