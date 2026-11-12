# Invoice Renamer

Drag-and-drop PDF invoices, get back a ZIP where every file is named:

```
YYYY-MM-DD - vendor - amount.pdf
2026-06-11 - milliporesigma - 689.pdf
2026-08-25 - mcmastercarr - 173 USD.pdf
```

- **Date** = the invoice/receipt date printed on the document (not the due date,
  shipment date, or the date you printed it).
- **Vendor** = a lowercase slug, generalised (`uoftmedstore`, `canadiantire`).
- **Amount** = grand total incl. tax and shipping, rounded half-up to whole dollars.
- **` USD`** appended only when the invoice is genuinely in US dollars.

## How extraction works

1. **Rules first.** 15 recurring lab suppliers have hard-coded patterns —
   deterministic, instant, free, and they never invent a number.
2. **Claude as fallback.** Anything unrecognised is sent to the Claude API, which
   returns the three fields as JSON. Scanned or image-only PDFs are sent to Claude as the PDF itself, so it reads them visually. Optional: without an API key the app still
   works, it just flags unknown vendors for you to fill in by hand.
3. **You review.** Every row lands in an editable table before the ZIP is built.

## Extras it handles

- PDFs that bundle several invoices (Amazon marketplace orders) — totals are
  summed, deduped by invoice number.
- Filename collisions (two same-day, same-amount invoices) — `(1)`, `(2)`.
- PDFs saved with an email MIME wrapper that makes most readers choke.
- European decimal commas (`108,17 $`).

## Run locally

```bash
pip install -r requirements.txt
streamlit run app.py
```

It opens at http://localhost:8501. For the Claude fallback, create
`.streamlit/secrets.toml` containing:

```toml
ANTHROPIC_API_KEY = "sk-ant-..."
```

## Deploy to Streamlit Community Cloud

1. Push `app.py`, `requirements.txt`, `.gitignore` to a GitHub repo.
   **Make the repo private** — invoices carry card last-fours, addresses and bank
   details, and an app from a private repo is private by default.
2. Go to share.streamlit.io → **Create app** → pick the repo → main file `app.py`.
3. (Optional) **Advanced settings → Secrets**, paste the same TOML line as above.
4. Deploy. You get `your-app-name.streamlit.app`.
5. **Settings → Sharing** to add viewers by email.

Never commit `.streamlit/secrets.toml` — it's already in `.gitignore`.

## Adding a vendor

Append a `Vendor(...)` entry to `VENDORS` in `app.py`. `marker` is any string
unique to that supplier's PDFs; `dates` and `totals` are lists of regex patterns
tried in order.
