# Deploying reading.adamthede.com

The generator never touches the network; deploying is a separate act, and the
FIRST production deploy is Adam's (house rule: agents never deploy to
production). After that first deploy proves out, the nightly wiring below
makes it automatic.

## One-time setup (Adam) — PLACEHOLDER FIRST

**Do not deploy the real content first.** Deploying before Access is
configured leaves the whole archive publicly readable for as long as the
dashboard work takes. The exposure is not hypothetical: the moment Cloudflare
issues a certificate for `reading.adamthede.com`, that hostname is published
to public Certificate Transparency logs, which are scraped continuously — new
hostnames get probed within minutes. (The `.pages.dev` URL is less exposed;
it sits under a wildcard cert and is not individually logged.)

So gate everything while the only public content is a blank page.

```bash
cd "Project - Instapaper Archive"

# 1. Create the project and deploy the BLANK placeholder
wrangler pages project create reading-adamthede --production-branch main
wrangler pages deploy site/placeholder --project-name reading-adamthede --branch main
```

**2. Now do all the gating, with nothing to leak:**

- Pages -> reading-adamthede -> Custom domains -> add `reading.adamthede.com`
  (the adamthede.com zone is already on Cloudflare: one click, automatic CNAME)
- Same screen: **"Disable access to pages.dev subdomain"** — do this BEFORE
  the Access app. An Access policy on the custom domain alone still leaves
  per-deployment `<hash>.reading-adamthede.pages.dev` previews open.
- Zero Trust -> Access -> Applications -> Add application (details below)
- **Verify**: load `https://reading.adamthede.com` in a private window and
  confirm you hit the Access login wall. Do this on the blank page, before
  there is anything worth protecting.

```bash
# 3. Only after the login wall is confirmed: build and deploy the real site
INSTAPAPER_VAULT_PATH="/Volumes/AST/Library/Articles/Instapaper-Matter-Archive" \
  .venv/bin/python site/generate.py --out _site
wrangler pages deploy _site --project-name reading-adamthede --branch main
```

`site/placeholder/` is committed (a blank dark page plus a
`Disallow: /` robots.txt) so this sequence is repeatable — for a rebuild of
the project, a staging copy, or any future private launch.

## Private-first (DECIDED 2026-08-19: Adam - like the rest of the portfolio)

The site launches behind Cloudflare Access; flipping public later is one
policy deletion, while unpublishing an indexed site is not.

First, REMOVE the side door instead of gating it: Pages project ->
Custom domains -> "Disable access to pages.dev subdomain". (Simpler and
more complete than adding pages.dev hostnames to the Access app - and an
Access app covering only the apex pages.dev would still leave
per-deployment `<hash>.reading-adamthede.pages.dev` previews open.)

Then Dashboard -> Zero Trust -> Access -> Applications -> Add application:

- Type: Self-hosted; domain `reading.adamthede.com`
- Policy: Allow -> Include -> Emails -> athede@gmail.com. (Login happens
  via the One-time PIN method configured under Zero Trust ->
  Settings -> Authentication - a separate screen from the policy.)
- Session duration: 1 month is reasonable for a personal site.

Wrangler deploys are unaffected - Access gates viewers, not deploys, so the
future nightly deploy leg needs no credentials for this.


## Nightly wiring (after the first deploy works)

Per the audit: the Matter plist should invoke a wrapper script rather than
gaining more arguments, with the deploy leg inside the same heartbeat -
a successful sync followed by a failed deploy must report as a failure.
Two gotchas recorded there:

- Add `~/.volta/bin` to the plist PATH or wrangler will not resolve under
  launchd.
- Extend `nightly-heartbeat.json`, do not add a second heartbeat.

That wiring is deliberately NOT in this PR - it lands as its own change once
the manual deploy has proven the project + domain, so a broken deploy leg
can never take down the proven sync/enrich/rebuild chain while it is being
introduced.

## `_site` is disposable

Never commit it. Regenerate any time: the synthesis files + this script are
the only inputs.
