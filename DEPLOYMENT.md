# NERVA Kit · Deployment Guide

Three options, ordered by effort. Pick one. You can always upgrade later.

---

## Option A · Local only (zero deployment, 2 minutes)

The simplest path. Everything lives on your laptop.

1. Download all five files to a folder you'll remember — say `~/Documents/nerva-kit/`.
2. Double-click `nerva-verdict-card.html` to see what a verdict looks like.
3. Double-click `nerva-ledger.html` to open your ledger. Bookmark this tab.
4. Open `NERVA_DECISION_PROMPT.md` in any text editor, select all, copy.
5. In Claude.ai, create a new Project called *NERVA Decisions*. Paste the prompt into Project Instructions. Save.

That's it. The ledger uses your browser's localStorage, so receipts persist across sessions on this device.

**Limitation:** the ledger only lives in this one browser. To use it on your phone or another machine, you'll need to export the JSON and import it on the other device manually — or use Option B.

---

## Option B · GitHub + Vercel (recommended, ~20 minutes)

This is the right path given your existing setup (anthonycharts.com, kc-intel.vercel.app, nerva-v10.vercel.app are all on this stack). Same workflow you already know.

### Step 1 · Create the repo

On GitHub, create a new repository. Two naming conventions worth thinking about:

- **`CaptainMig/nerva-kit`** — fast, uses your existing personal account.
- **`starpoint-llc/nerva-kit`** (new org) — cleaner for the public-facing Starpoint brand, but you'd need to create the org first. Worth doing eventually, but not required for the first push.

Pick one. I'd suggest `CaptainMig/nerva-kit` as **private** for now. Make it public later when you're ready to point investors and partners at it.

### Step 2 · Push the files

Drop these five files into the repo root:

```
nerva-kit/
├── README.md
├── NERVA_DECISION_PROMPT.md
├── nerva_kernel.py
├── nerva-verdict-card.html
└── nerva-ledger.html
```

If you're using GitHub's web editor (the Windows-friendly approach you've been using for anthonycharts), drag-and-drop works fine. Each file is small.

### Step 3 · Deploy to Vercel

In Vercel, click *Add New → Project*, connect to the GitHub repo, accept defaults. No build configuration needed — these are static files.

You'll get a URL like `nerva-kit.vercel.app`. You can also point a subdomain if you have one (`kit.starpoint.com`, `ledger.starpoint.com`, etc.).

### Step 4 · Use the live URLs

- `https://nerva-kit.vercel.app/nerva-verdict-card.html` — the verdict reference
- `https://nerva-kit.vercel.app/nerva-ledger.html` — your live ledger, bookmarkable, works on phone and desktop

The ledger still uses browser localStorage, so receipts are per-device. To sync across devices, use the export/import JSON buttons. (Cloud sync is Option C below.)

### Step 5 · Update workflow

When you tweak the prompt or kernel, commit the change to GitHub. Vercel auto-redeploys. You always have the latest live.

---

## Option C · Cross-device sync (future, when you need it)

If you start running the ledger seriously and want receipts to sync across desktop and phone automatically, the build is:

- Replace `localStorage` calls in `nerva-ledger.html` with calls to a small backend.
- Easiest backends: **Vercel KV** (Redis-flavored, native to Vercel), **Supabase** (Postgres + auth, generous free tier), or **Cloudflare Workers KV**.
- Adds: a simple auth wall so only you can read your ledger.

This is maybe a half-day of build. Don't do it until you've logged 20–30 receipts in Option B and know you'll keep going. Premature.

---

## What to do this week

1. Tonight or tomorrow: Option A. Five files in a folder, prompt in a Claude Project, ledger bookmarked. Score one decision — the Antonelli trade, or anything else on your desk — and log the receipt.
2. This weekend: Option B. Spin up the GitHub repo and Vercel deploy. Now the ledger works on your phone.
3. Six weeks from now, after 15–20 receipts: decide whether the discipline is real for you. If yes, Option C. If no, you've lost a weekend and gained a verdict template.

---

## Privacy note (read once)

The ledger stores everything locally in your browser. It does **not** send receipts to any server, including Anthropic's. The privacy risk is entirely upstream of the ledger — at the moment you paste a decision into an LLM. The deployment-mode guidance in `NERVA_DECISION_PROMPT.md` §0 is the relevant rule there.

If you ever screenshot a verdict card containing sensitive details (client names, financial figures), treat that screenshot as confidential the same way you'd treat the underlying decision.

---

— Starpoint LLC · NERVA v11
