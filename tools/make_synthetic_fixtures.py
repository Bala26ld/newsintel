"""Generate the synthetic EDGAR fixtures for the fictional MVP universe.

Real filings cannot be fetched from the build environment, so these fixtures imitate the shape of
8-K primary documents (cover page, items, signature) for fictional companies. The adapter writes
real fixtures in exactly the same JSON shape when it runs live, so the replay path is identical.

Run:  python tools/make_synthetic_fixtures.py
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from newsintel.adapters.base import FixtureRecorder, RawSnapshot  # noqa: E402

OUT = Path(__file__).resolve().parents[1] / "fixtures"

COVER = """<html><head><title>{form}</title></head><body>
<div>UNITED STATES SECURITIES AND EXCHANGE COMMISSION</div>
<div>Washington, D.C. 20549</div>
<div><b>FORM {form}</b></div>
<div>CURRENT REPORT</div>
<div>Pursuant to Section 13 or 15(d) of the Securities Exchange Act of 1934</div>
<div>Date of Report (Date of earliest event reported): {report_date}</div>
<div><b>{registrant}</b></div>
<div>(Exact name of registrant as specified in its charter)</div>
<table><tr><td>Delaware</td><td>001-{file_no}</td><td>{ein}</td></tr></table>
<div>(State or other jurisdiction of incorporation) (Commission File Number) (IRS Employer Identification No.)</div>
"""

SIGN = """<div><b>SIGNATURES</b></div>
<div>Pursuant to the requirements of the Securities Exchange Act of 1934, the registrant has duly caused this report to be signed on its behalf by the undersigned hereunto duly authorized.</div>
<div>{registrant}</div>
<div>Date: {report_date}</div>
</body></html>"""


def item(code: str, title: str, paragraphs: list[str]) -> str:
    html = f"<div><b>Item {code} {title}</b></div>\n"
    for p in paragraphs:
        html += f"<p>{p}</p>\n"
    return html


ENTITIES = {
    "HALD": ("Halvard Dynamics Corporation", "0009990001", "41001"),
    "MRFD": ("Meridian Foods, Inc.", "0009990002", "41002"),
    "ORBS": ("Orbital Semiconductor, Inc.", "0009990003", "41003"),
    "PNCB": ("Pinecrest Bancorp, Inc.", "0009990004", "41004"),
    "TSRO": ("Tessaro Pharmaceuticals, Inc.", "0009990005", "41005"),
    "NWLG": ("Northwind Logistics Holdings, Inc.", "0009990006", "41006"),
    "CSTL": ("Castellan Energy Corporation", "0009990007", "41007"),
    "VLMT": ("Velmont Software, Inc.", "0009990008", "41008"),
    "BRKW": ("Brookwater Utilities, Inc.", "0009990009", "41009"),
    "ASHM": ("Ashmere Medical Devices, Inc.", "0009990010", "41010"),
}

# (ticker, form, report_date, items, body_html_parts, acceptance_utc, retrieved_offset_s, label)
CASES: list[dict] = []


def add(ticker, form, report_date, items, parts, accepted, label, accession=None, delay=5):
    CASES.append(
        dict(
            ticker=ticker,
            form=form,
            report_date=report_date,
            items=items,
            parts=parts,
            accepted=accepted,
            label=label,
            accession=accession,
            delay=delay,
        )
    )


# 1. Halvard earnings (reference revenue for the later contract)
add(
    "HALD",
    "8-K",
    "October 6, 2026",
    ["2.02", "9.01"],
    [
        item(
            "2.02",
            "Results of Operations and Financial Condition.",
            [
                'On October 6, 2026, Halvard Dynamics Corporation (the "Company") issued a press release announcing its financial results for the third quarter ended September 30, 2026. Revenue for the quarter was $4.2 billion, compared with $3.8 billion in the prior-year period. Net income was $512 million, or $1.84 per diluted share.',
                "The Company expects fourth quarter revenue of approximately $4.4 billion.",
                "The information in this Item 2.02 is being furnished and shall not be deemed filed for purposes of Section 18 of the Exchange Act.",
            ],
        ),
        item("9.01", "Financial Statements and Exhibits.", ["99.1 Press release dated October 6, 2026."]),
    ],
    "2026-10-06T20:05:12Z",
    "earnings_q3",
)
# 2. Halvard contract award ($1.1bn, 5-year, DOE) -> verified, publishes
add(
    "HALD",
    "8-K",
    "October 9, 2026",
    ["1.01", "9.01"],
    [
        item(
            "1.01",
            "Entry into a Material Definitive Agreement.",
            [
                'On October 9, 2026, Halvard Dynamics Corporation (the "Company") entered into a definitive agreement with the United States Department of Energy pursuant to which the Company will design and deliver grid-scale storage systems. The contract is valued at approximately $1.1 billion over a 5-year term and will be performed by the Company\'s Energy Systems segment.',
                "The foregoing description does not purport to be complete and is qualified in its entirety by reference to the agreement, a copy of which will be filed with the Company's next periodic report.",
            ],
        ),
        item("9.01", "Financial Statements and Exhibits.", ["99.1 Press release dated October 9, 2026."]),
    ],
    "2026-10-09T13:31:40Z",
    "contract_award_doe",
)
# 3. The same document fetched again one hour later (exact duplicate -> dropped at ingest)
add(
    "HALD",
    "8-K",
    "October 9, 2026",
    ["1.01", "9.01"],
    None,  # copy of #2
    "2026-10-09T13:31:40Z",
    "contract_award_doe_refetch",
    accession="SAME:contract_award_doe",
    delay=3600,
)
# 4. Halvard 8.01 repeating the award in a press-release-style item (structural duplicate within 72h -> merged)
add(
    "HALD",
    "8-K",
    "October 10, 2026",
    ["8.01"],
    [
        item(
            "8.01",
            "Other Events.",
            [
                "On October 10, 2026, Halvard Dynamics Corporation issued a press release announcing that it has been awarded a contract by the United States Department of Energy valued at approximately $1.1 billion over a 5-year term for grid-scale storage systems.",
            ],
        )
    ],
    "2026-10-10T12:02:10Z",
    "contract_award_doe_repeat",
)
# 5. Meridian acquisition ($350m)
add(
    "MRFD",
    "8-K",
    "October 7, 2026",
    ["2.01", "9.01"],
    [
        item(
            "2.01",
            "Completion of Acquisition or Disposition of Assets.",
            [
                'On October 7, 2026, Meridian Foods, Inc. (the "Company") completed the previously announced acquisition of all of the outstanding equity of Clearwater Organics, LLC for an aggregate purchase price of approximately $350 million in cash, subject to customary adjustments. Clearwater Organics is a producer of organic dairy products.',
            ],
        )
    ],
    "2026-10-07T21:14:03Z",
    "acquisition_clearwater",
)
# 6. Meridian CFO change
add(
    "MRFD",
    "8-K",
    "October 12, 2026",
    ["5.02"],
    [
        item(
            "5.02",
            "Departure of Directors or Certain Officers; Election of Directors; Appointment of Certain Officers.",
            [
                "On October 12, 2026, Dana R. Whitlock resigned as Chief Financial Officer of Meridian Foods, Inc. effective October 31, 2026. On the same date, the Board of Directors appointed Samuel Ortega as Chief Financial Officer effective November 1, 2026. Mr. Ortega, age 52, previously served as Senior Vice President, Finance.",
            ],
        )
    ],
    "2026-10-12T20:45:55Z",
    "cfo_change",
)
# 7. Orbital earnings (net loss)
add(
    "ORBS",
    "8-K",
    "October 8, 2026",
    ["2.02", "9.01"],
    [
        item(
            "2.02",
            "Results of Operations and Financial Condition.",
            [
                "On October 8, 2026, Orbital Semiconductor, Inc. announced results for the fourth quarter ended August 31, 2026. Revenue was $612 million. Net loss was $48 million, or $0.31 per diluted share.",
            ],
        )
    ],
    "2026-10-08T20:10:00Z",
    "earnings_loss",
)
# 8. Orbital contract with undisclosed value -> publishes with contract value not established
add(
    "ORBS",
    "8-K",
    "October 13, 2026",
    ["1.01"],
    [
        item(
            "1.01",
            "Entry into a Material Definitive Agreement.",
            [
                "On October 13, 2026, Orbital Semiconductor, Inc. entered into a multi-year supply agreement with Kestrel Automotive Systems under which the Company will supply radar sensor modules. The financial terms of the agreement were not disclosed.",
            ],
        )
    ],
    "2026-10-13T11:20:30Z",
    "contract_undisclosed",
)
# 9. Pinecrest credit agreement -> financing, not a commercial contract: no event
add(
    "PNCB",
    "8-K",
    "October 8, 2026",
    ["1.01", "2.03"],
    [
        item(
            "1.01",
            "Entry into a Material Definitive Agreement.",
            [
                "On October 8, 2026, Pinecrest Bancorp, Inc. entered into a credit agreement with a syndicate of lenders providing for a $200 million revolving credit facility maturing in 2031.",
            ],
        )
    ],
    "2026-10-08T15:00:00Z",
    "credit_agreement",
)
# 10. Tessaro earnings, then an amended 8-K/A correcting revenue -> correction alert v2
add(
    "TSRO",
    "8-K",
    "October 9, 2026",
    ["2.02"],
    [
        item(
            "2.02",
            "Results of Operations and Financial Condition.",
            [
                "On October 9, 2026, Tessaro Pharmaceuticals, Inc. reported results for the third quarter ended September 30, 2026. Net revenues were $214 million. Net income was $19 million, or $0.22 per diluted share.",
            ],
        )
    ],
    "2026-10-09T20:30:00Z",
    "earnings_tsro",
)
add(
    "TSRO",
    "8-K/A",
    "October 9, 2026",
    ["2.02"],
    [
        item(
            "2.02",
            "Results of Operations and Financial Condition.",
            [
                "This Amendment No. 1 corrects a typographical error in the Current Report on Form 8-K filed on October 9, 2026. Tessaro Pharmaceuticals, Inc. reported results for the third quarter ended September 30, 2026. Net revenues were $241 million. Net income was $19 million, or $0.22 per diluted share.",
            ],
        )
    ],
    "2026-10-10T14:05:00Z",
    "earnings_tsro_amended",
)
# 11. Northwind earnings using the 'increased ... to' phrasing, then a contract
add(
    "NWLG",
    "8-K",
    "October 7, 2026",
    ["2.02"],
    [
        item(
            "2.02",
            "Results of Operations and Financial Condition.",
            [
                "Northwind Logistics Holdings, Inc. today reported results for the third quarter ended September 30, 2026. Revenue increased 12% to $890 million. Net income of $41 million, or $0.58 per diluted share, compared with $35 million in the prior-year quarter.",
            ],
        )
    ],
    "2026-10-07T11:00:00Z",
    "earnings_nwlg",
)
add(
    "NWLG",
    "8-K",
    "October 14, 2026",
    ["1.01"],
    [
        item(
            "1.01",
            "Entry into a Material Definitive Agreement.",
            [
                "On October 14, 2026, Northwind Logistics Holdings, Inc. entered into a master services agreement with Skyline Freight Partners LLC for dedicated contract carriage services. The agreement has a total value of approximately $95 million over 3 years.",
            ],
        )
    ],
    "2026-10-14T12:40:00Z",
    "contract_skyline",
)
# 12. Castellan: earnings, then a contract worth 60% of annualised revenue -> held for review
add(
    "CSTL",
    "8-K",
    "October 6, 2026",
    ["2.02"],
    [
        item(
            "2.02",
            "Results of Operations and Financial Condition.",
            [
                "On October 6, 2026, Castellan Energy Corporation reported results for the quarter ended September 30, 2026. Revenues were $1.5 billion. Net income was $120 million, or $0.95 per diluted share.",
            ],
        )
    ],
    "2026-10-06T21:00:00Z",
    "earnings_cstl",
)
add(
    "CSTL",
    "8-K",
    "October 15, 2026",
    ["1.01"],
    [
        item(
            "1.01",
            "Entry into a Material Definitive Agreement.",
            [
                "On October 15, 2026, Castellan Energy Corporation entered into a long-term supply agreement with Meridian Power Cooperative valued at approximately $3.6 billion over a 10-year term for the delivery of natural gas and firm transportation capacity.",
            ],
        )
    ],
    "2026-10-15T11:05:00Z",
    "contract_large",
)
# 13. Velmont: management appointment only
add(
    "VLMT",
    "8-K",
    "October 13, 2026",
    ["5.02"],
    [
        item(
            "5.02",
            "Departure of Directors or Certain Officers; Election of Directors; Appointment of Certain Officers.",
            [
                "On October 13, 2026, the Board of Directors of Velmont Software, Inc. appointed Priya Raghunathan as Chief Technology Officer effective October 20, 2026.",
            ],
        )
    ],
    "2026-10-13T21:15:00Z",
    "cto_appointed",
)
# 14. Brookwater earnings with no EPS line
add(
    "BRKW",
    "8-K",
    "October 14, 2026",
    ["2.02"],
    [
        item(
            "2.02",
            "Results of Operations and Financial Condition.",
            [
                "On October 14, 2026, Brookwater Utilities, Inc. announced results for the third quarter ended September 30, 2026. Revenues were $780 million and net income was $66 million.",
            ],
        )
    ],
    "2026-10-14T20:50:00Z",
    "earnings_brkw",
)
# 15. Ashmere acquisition with a stake percentage
add(
    "ASHM",
    "8-K",
    "October 15, 2026",
    ["2.01"],
    [
        item(
            "2.01",
            "Completion of Acquisition or Disposition of Assets.",
            [
                "On October 15, 2026, Ashmere Medical Devices, Inc. completed the acquisition of 80% of the outstanding shares of Lumen Surgical Robotics, Inc. for cash consideration of approximately $410 million.",
            ],
        )
    ],
    "2026-10-15T12:30:00Z",
    "acquisition_lumen",
)


def main() -> None:
    rec = FixtureRecorder(root=OUT, enabled=True)
    labels: dict[str, dict] = {}
    bodies: dict[str, str] = {}
    n = 0
    for i, c in enumerate(CASES):
        name, cik, ein = ENTITIES[c["ticker"]]
        if c["parts"] is None:  # exact re-fetch of an earlier case
            src_label = c["accession"].split(":")[1]
            html = bodies[src_label]
            accession = labels[src_label]["accession"]
        else:
            html = (
                COVER.format(form=c["form"], report_date=c["report_date"], registrant=name, file_no=ein, ein=f"86-{ein}0")
                + "".join(c["parts"])
                + SIGN.format(registrant=name, report_date=c["report_date"])
            )
            accession = f"{cik}-26-{100000 + i:06d}"
            bodies[c["label"]] = html
        accepted = datetime.fromisoformat(c["accepted"].replace("Z", "+00:00"))
        retrieved = accepted + timedelta(seconds=c["delay"])
        cik_int = int(cik)
        url = f"https://www.sec.gov/Archives/edgar/data/{cik_int}/{accession.replace('-', '')}/form{c['form'].replace('/', '').lower()}.htm"
        snap = RawSnapshot(
            source_id="sec_edgar",
            url=url,
            body=html.encode("utf-8"),
            mime="text/html",
            retrieved_at=retrieved.astimezone(UTC),
            source_published_at=accepted,
            headers={"content-type": "text/html", "x-fixture": "synthetic"},
            meta={
                "cik": cik,
                "registrant": name,
                "accession": accession,
                "form": c["form"],
                "filing_date": accepted.date().isoformat(),
                "acceptance": c["accepted"],
                "primary_document": url.rsplit("/", 1)[1],
                "items": c["items"],
                "tickers": [c["ticker"]],
                "synthetic": True,
                "label": c["label"],
            },
        )
        p = rec.record(snap)
        labels[c["label"]] = {"fixture": str(p.relative_to(OUT)), "accession": accession, "content_hash": snap.content_hash}
        n += 1
    (OUT / "golden" / "fixture_index.json").parent.mkdir(parents=True, exist_ok=True)
    (OUT / "golden" / "fixture_index.json").write_text(json.dumps(labels, indent=1))
    print(f"wrote {n} fixtures under {OUT}")


if __name__ == "__main__":
    main()
