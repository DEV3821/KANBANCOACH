"""Deterministic NT UltraRad regression test for the evidence pipeline.

Validates known v10/v11/v12 pattern without any live Outlook or mailbox access.
Uses existing evidence artifacts from the preserved runs.
"""

from __future__ import annotations

import json
from pathlib import Path

# ── Known evidence paths ──
EVD_ROOT = Path("C:/Tools/SAMI Kanban Coach/runtime/apply/evidence")
V10_DIR = EVD_ROOT / "target_thread"
V11_DIR = EVD_ROOT / "sr521202"
V12_DIR = EVD_ROOT / "v12_ocr_sent"


def run_regression(output_path: str | Path | None = None) -> dict:
    """Run deterministic regression test using preserved evidence.

    Validates the known NT UltraRad pattern from v10/v11/v12 pilots.
    Does NOT access Outlook or mutate any state.

    Returns dict with test results.
    """
    passed = 0
    failed = 0
    results = []

    def check(name: str, condition: bool, detail: str = ""):
        nonlocal passed, failed
        if condition:
            passed += 1
            results.append({"check": name, "status": "PASS", "detail": detail})
        else:
            failed += 1
            results.append({"check": name, "status": "FAIL", "detail": detail})

    # ── v10: UltraRad Inbox thread ──
    v10_sitrep = V10_DIR / "export" / "v10_sitrep_report.txt"
    check("v10 SITREP exists", v10_sitrep.exists(), str(v10_sitrep))

    v10_atts = list((V10_DIR / "export").glob("*.png")) + list((V10_DIR / "export").glob("*.jpg"))
    check("v10 has 8 attachments", len(v10_atts) == 8, f"found {len(v10_atts)}")

    if v10_sitrep.exists():
        text = v10_sitrep.read_text(encoding="utf-8")
        check("v10: target thread found", "Target thread found: YES" in text)
        check("v10: thread count 2", "Target thread message count: 2" in text)
        check("v10: conversation ID found", "ConversationID found: YES" in text)
        check("v10: attachment count 4", "Target-thread attachment count: 4" in text)
        check("v10: parse status no_text", "no_text" in text or "image" in text.lower())
        check("v10: NEC in email bodies", "NEC: FOUND" in text)
        check("v10: Jason in email bodies", "Jason: FOUND" in text)
        check("v10: Daniel Schroeder in bodies", "Daniel Schroeder: FOUND" in text)
        check("v10: NTGMIPRDG in bodies", "NTGMIPRDG: FOUND" in text)
        check("v10: SRV-3890870 NOT found", "SRV-3890870: not found" in text)
        check("v10: REQ2026637 NOT found", "REQ2026637: not found" in text)
        check("v10: IPs NOT found",
              "10.2.240: not found" in text and "10.2.39.243: not found" in text)
        check("v10: search status inconclusive",
              "target_thread_found_attachments_inconclusive" in text)
        check("v10: mailboxMutated=false", "mailboxMutated: False" in text)
        check("v10: kanbanWritePerformed=false", "kanbanWritePerformed: False" in text)

    # ── v11: SR# 521202 Tasmania thread ──
    v11_sitrep = V11_DIR / "export" / "v11_sitrep_report.txt"
    check("v11 SITREP exists", v11_sitrep.exists(), str(v11_sitrep))

    v11_atts = list((V11_DIR / "export").glob("*.png")) + list((V11_DIR / "export").glob("*.jpg"))
    check("v11 has 4 attachments", len(v11_atts) == 4, f"found {len(v11_atts)}")

    if v11_sitrep.exists():
        text = v11_sitrep.read_text(encoding="utf-8")
        check("v11: thread found", "SR# 521202 thread found: YES" in text)
        check("v11: thread count 2", "Thread message count: 2" in text)
        check("v11: conversation ID found", "ConversationID found: YES" in text)
        check("v11: NEC in bodies", "NEC: FOUND" in text)
        check("v11: IPESC in bodies", "IPESC: FOUND" in text)
        check("v11: THS in bodies (Tasmania)", "THS: FOUND" in text)
        check("v11: SRV-3890870 NOT found", "SRV-3890870: not found" in text)
        check("v11: NT IPs NOT found",
              "10.2.240: not found" in text and "10.2.39.243: not found" in text)
        check("v11: search status inconclusive",
              "sr521202_thread_found_attachments_inconclusive" in text)

    # ── v12: Sent Items / SRV-3890870 thread ──
    v12_sitrep = V12_DIR / "v12_sitrep_report.txt"
    check("v12 SITREP exists", v12_sitrep.exists(), str(v12_sitrep))

    v12_xlsx = list(V12_DIR.glob("*.xlsx"))
    check("v12 has 2 xlsx documents", len(v12_xlsx) == 2, f"found {len(v12_xlsx)}")

    # Parse xlsx for specific evidence
    if v12_xlsx:
        import openpyxl
        for xl in v12_xlsx:
            name = xl.name
            wb = openpyxl.load_workbook(xl, read_only=True, data_only=True)
            all_text = ""
            for ws in wb.worksheets:
                for row in ws.iter_rows(values_only=True):
                    all_text += " | ".join(str(c) for c in row if c is not None) + "\n"
            wb.close()

            if "NTG_S2S_VPN" in name:
                check("NTG S2S VPN: contains 'NTG SITE TO SITE VPN'",
                      "NTG SITE TO SITE VPN" in all_text)
                check("NTG S2S VPN: contains SA Health contact",
                      "nicholas.thong" in all_text.lower() or "Nicholas Thong" in all_text)
                check("NTG S2S VPN: IKE v2", "IKE v2" in all_text or "IKEv2" in all_text)
                check("NTG S2S VPN: AES-256-GCM", "AES-256-GCM" in all_text)

            if "Firewall_Technical" in name:
                check("DHSA Firewall: SRV-3890870 found", "SRV-3890870" in all_text)
                check("DHSA Firewall: 10.2.240.207", "10.2.240.207" in all_text,
                      "DOHWULTRMAP01P")
                check("DHSA Firewall: 10.2.240.208", "10.2.240.208" in all_text,
                      "DOHWULTRMAP02P")
                check("DHSA Firewall: 10.2.240.209", "10.2.240.209" in all_text,
                      "DOHWULTRMAP03P")
                check("DHSA Firewall: 10.2.39.243", "10.2.39.243" in all_text,
                      "NTH-BREAK-GLASS")
                check("DHSA Firewall: 10.2.65.105", "10.2.65.105" in all_text,
                      "NTH-PACS-INBOUND-VIP")
                check("DHSA Firewall: DICOM TCP 104", "TCP 104" in all_text)
                check("DHSA Firewall: DICOM TCP 2104", "TCP 2104" in all_text)
                check("DHSA Firewall: REQ-TBD (not REQ2026637)", "REQ -TBD" in all_text,
                      "REQ number was TBD when form was filled — REQ2026637 is separate")
                check("DHSA Firewall: NEC not in spreadsheet", "NEC" not in all_text,
                      "NEC found in email bodies only, not the spreadsheet itself")
                check("DHSA Firewall: has Objects tab with NT UltraRad gateways",
                      "DOHWULTRMAP01P" in all_text)
                check("DHSA Firewall: has Rules tab with SRV-3890870",
                      "NT UltraRad / SA Stroke PACS VPN rules" in all_text)
                check("DHSA Firewall: VPN peer 203.26.120.226",
                      "203.26.120.226" in all_text)

    # ── Pipeline ep_ run artifacts ──
    ep_runs = sorted(EVD_ROOT.glob("ep_*"))
    if ep_runs:
        latest = max(ep_runs, key=lambda p: p.name)
        for fn in ["evidence_manifest.json", "search_results.json",
                    "local_model_input.json", "local_model_output.json",
                    "attachment_index.json"]:
            check(f"Pipeline: {fn} exists", (latest / fn).exists())
        # Check manifest classification
        mf = latest / "evidence_manifest.json"
        if mf.exists():
            m = json.loads(mf.read_text(encoding="utf-8"))
            status = m.get("search_results", {}).get("classified_status", "")
            check(f"Pipeline status: {status}", "attachment_evidence" in status or "sent_items" in status)

        mo = latest / "local_model_output.json"
        if mo.exists():
            o = json.loads(mo.read_text(encoding="utf-8")).get("output", {})
            check("Model: review_draft rec", o.get("apply_recommendation") == "review_draft",
                  f"got {o.get('apply_recommendation')}")
            check("Model: confidence > 0.5", o.get("confidence", 0) > 0.5,
                  f"confidence={o.get('confidence')}")
            check("Model: requires_human_approval=true", o.get("requires_human_approval") is True)
            check("Model: mailboxMutated=false", o.get("mailboxMutated") is False)
            check("Model: kanbanWritePerformed=false", o.get("kanbanWritePerformed") is False)
    else:
        results.append({"check": "Pipeline run exists", "status": "SKIP",
                        "detail": "no ep_* runs found — run evidence-search first"})

    # ── Draft exists ──
    drafts = list(Path("C:/Tools/SAMI Kanban Coach/runtime/apply/drafts").glob("draft_*.json"))
    check("Draft file exists", len(drafts) >= 1, f"found {len(drafts)}")

    # ── Safety checks (config) ──
    cfg_path = Path("C:/Tools/SAMI Kanban Coach/config/settings.json")
    if cfg_path.exists():
        c = json.loads(cfg_path.read_text(encoding="utf-8"))
        check("Config: mailbox_search_enabled=False", c.get("mailbox_search_enabled") is False)
        check("Config: recent_days=180", c.get("mailbox_search_recent_days") == 180)
        check("Config: allow_kanban_apply=False", c.get("allow_kanban_apply") is False)

    # ── Summary ──
    summary = {
        "test_name": "NT UltraRad Evidence Regression",
        "passed": passed,
        "failed": failed,
        "skipped": len(results) - passed - failed,
        "total": len(results),
        "results": results,
        "all_passed": failed == 0,
    }

    if output_path:
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(summary, indent=2, ensure_ascii=False))
        print(f"  Results written to {out}")

    return summary


if __name__ == "__main__":
    r = run_regression()
    print(f"\n{'='*60}")
    print(f"NT UltraRad Regression Test: {r['passed']}/{r['total']} passed, {r['failed']} failed")
    print(f"{'='*60}")
    for res in r["results"]:
        status_sym = "✓" if res["status"] == "PASS" else "✗" if res["status"] == "FAIL" else "—"
        print(f"  {status_sym} {res['check']}")
        if res["detail"]:
            print(f"      {res['detail']}")
    print(f"\nResult: {'ALL PASSED' if r['all_passed'] else 'SOME FAILED'}")
