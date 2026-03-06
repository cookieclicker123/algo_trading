"""
Search all recall records for headlines containing 'oversubscribed' or 'private placement'.
Reports: ticker, headline, date, MFE, MAE, 10min price change, classification, filter reason.
"""
import json
import glob
import os
import re
from datetime import datetime


def search_recall_records():
    base_dir = os.path.join(os.path.dirname(__file__), "..", "tmp", "statistics", "recall")
    base_dir = os.path.abspath(base_dir)

    pattern = os.path.join(base_dir, "**", "*.json")
    files = glob.glob(pattern, recursive=True)

    keywords = re.compile(r"oversubscribed|private\s+placement", re.IGNORECASE)

    matches = []

    for filepath in sorted(files):
        try:
            with open(filepath, "r") as f:
                data = json.load(f)
        except (json.JSONDecodeError, IOError):
            continue

        records = data.get("records", [])
        for rec in records:
            title = rec.get("title", "")
            if not keywords.search(title):
                continue

            # Extract date from published_at or received_at or file path
            date_str = rec.get("published_at") or rec.get("received_at") or ""
            if date_str:
                try:
                    dt = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
                    date_display = dt.strftime("%Y-%m-%d %H:%M")
                except Exception:
                    date_display = date_str[:16]
            else:
                date_display = "unknown"

            tickers = rec.get("tickers", [])
            ticker_str = ", ".join(tickers) if tickers else "N/A"

            # Classification
            ai_class = rec.get("ai_classification", None)
            classification_display = ai_class.upper() if ai_class else "NOT CLASSIFIED"

            # Filter reason (prefilter or postfilter)
            filter_reason = rec.get("filter_reason", None)
            postfilter_reason = rec.get("postfilter_reason", None)
            reason_display = postfilter_reason or filter_reason or "none"

            # 10-min price check
            pc10 = rec.get("price_check_10min", {}) or {}
            pct_change_10min = pc10.get("percent_change")
            if pct_change_10min is not None:
                pct_change_10min_str = f"{pct_change_10min:+.2f}%"
            else:
                pct_change_10min_str = "N/A"

            # MFE (max favorable excursion) = highest_price_during_hold
            mfe_data = rec.get("highest_price_during_hold", {}) or {}
            mfe_pct = mfe_data.get("percent_gain_from_entry")
            if mfe_pct is not None:
                mfe_str = f"{mfe_pct:+.2f}%"
                mfe_time = f"at {mfe_data.get('minute', '?')}m{mfe_data.get('second', '?')}s"
            else:
                mfe_str = "N/A"
                mfe_time = ""

            # MAE (max adverse excursion) = max_adverse_excursion
            mae_data = rec.get("max_adverse_excursion", {}) or {}
            mae_pct = mae_data.get("percent_loss_from_entry")
            if mae_pct is not None:
                mae_str = f"{mae_pct:+.2f}%"
                mae_time = f"at {mae_data.get('minute', '?')}m{mae_data.get('second', '?')}s"
            else:
                mae_str = "N/A"
                mae_time = ""

            # Initial NBBO for context
            nbbo = rec.get("initial_nbbo", {}) or {}
            ask_price = nbbo.get("ask")
            spread_pct = nbbo.get("spread_pct")

            # Session
            session = rec.get("session", "unknown")

            # Was it traded?
            is_traded = rec.get("is_traded", False)

            # Headline type if classified
            headline_type = rec.get("headline_type", None)

            matches.append({
                "date": date_display,
                "ticker": ticker_str,
                "title": title,
                "session": session,
                "classification": classification_display,
                "filter_reason": reason_display,
                "pct_change_10min": pct_change_10min_str,
                "mfe": mfe_str,
                "mfe_time": mfe_time,
                "mae": mae_str,
                "mae_time": mae_time,
                "ask": ask_price,
                "spread_pct": spread_pct,
                "is_traded": is_traded,
                "headline_type": headline_type,
                "raw_pct_10min": pct_change_10min,
                "raw_mfe": mfe_pct,
            })

    # Sort by date
    matches.sort(key=lambda x: x["date"])

    # Print results
    print(f"\n{'='*120}")
    print(f"RECALL SEARCH: 'oversubscribed' OR 'private placement' headlines")
    print(f"Files scanned: {len(files)}")
    print(f"Matches found: {len(matches)}")
    print(f"{'='*120}\n")

    if not matches:
        print("No matching records found.")
        return

    for i, m in enumerate(matches, 1):
        traded_tag = " [TRADED]" if m["is_traded"] else ""
        hl_tag = f" [type: {m['headline_type']}]" if m["headline_type"] else ""
        print(f"--- #{i}{traded_tag}{hl_tag} ---")
        print(f"  Date:           {m['date']}  ({m['session']})")
        print(f"  Ticker:         {m['ticker']}")
        print(f"  Headline:       {m['title']}")
        print(f"  Classification: {m['classification']}")
        print(f"  Filter reason:  {m['filter_reason']}")
        if m["ask"]:
            spread_info = f"  (spread: {m['spread_pct']:.1f}%)" if m["spread_pct"] else ""
            print(f"  Ask at recv:    ${m['ask']:.2f}{spread_info}")
        print(f"  10min change:   {m['pct_change_10min']}")
        print(f"  MFE (high):     {m['mfe']}  {m['mfe_time']}")
        print(f"  MAE (low):      {m['mae']}  {m['mae_time']}")
        print()

    # Summary statistics
    print(f"\n{'='*120}")
    print("SUMMARY")
    print(f"{'='*120}")

    classified_imminent = [m for m in matches if m["classification"] == "IMMINENT"]
    classified_ignore = [m for m in matches if m["classification"] in ("IGNORE", "ROUTINE", "SPECULATIVE")]
    not_classified = [m for m in matches if m["classification"] == "NOT CLASSIFIED"]
    traded = [m for m in matches if m["is_traded"]]

    print(f"  Total matches:      {len(matches)}")
    print(f"  Classified IMMINENT: {len(classified_imminent)}")
    print(f"  Classified IGNORE/ROUTINE/SPECULATIVE: {len(classified_ignore)}")
    print(f"  Not classified (prefiltered): {len(not_classified)}")
    print(f"  Actually traded:    {len(traded)}")

    # 10min stats for those with data
    with_10min = [m for m in matches if m["raw_pct_10min"] is not None]
    if with_10min:
        changes = [m["raw_pct_10min"] for m in with_10min]
        positive = [c for c in changes if c > 0]
        negative = [c for c in changes if c < 0]
        print(f"\n  10-min price changes (n={len(with_10min)}):")
        print(f"    Mean:    {sum(changes)/len(changes):+.2f}%")
        print(f"    Median:  {sorted(changes)[len(changes)//2]:+.2f}%")
        print(f"    Best:    {max(changes):+.2f}%")
        print(f"    Worst:   {min(changes):+.2f}%")
        print(f"    Positive: {len(positive)}/{len(with_10min)} ({100*len(positive)/len(with_10min):.0f}%)")

    # MFE stats
    with_mfe = [m for m in matches if m["raw_mfe"] is not None]
    if with_mfe:
        mfes = [m["raw_mfe"] for m in with_mfe]
        print(f"\n  MFE (max favorable excursion, n={len(with_mfe)}):")
        print(f"    Mean:    {sum(mfes)/len(mfes):+.2f}%")
        print(f"    Best:    {max(mfes):+.2f}%")
        print(f"    Worst:   {min(mfes):+.2f}%")

    # Filter reason breakdown
    print(f"\n  Filter reason breakdown:")
    reason_counts = {}
    for m in matches:
        reason = m["filter_reason"]
        # Normalize: strip numeric suffixes from latency/cap reasons for grouping
        base_reason = reason.split(":")[0] if ":" in reason else reason
        reason_counts[base_reason] = reason_counts.get(base_reason, 0) + 1
    for reason, count in sorted(reason_counts.items(), key=lambda x: -x[1]):
        print(f"    {reason}: {count}")

    print()


if __name__ == "__main__":
    search_recall_records()
