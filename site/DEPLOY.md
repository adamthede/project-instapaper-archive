# Deploying reading.adamthede.com

The generator never touches the network; deploying is a separate act, and the
FIRST production deploy is Adam's (house rule: agents never deploy to
production). After that first deploy proves out, the nightly wiring below
makes it automatic.

## One-time setup (Adam)

```bash
cd "Project - Instapaper Archive"

# 1. Build the site
INSTAPAPER_VAULT_PATH="/Volumes/AST/Library/Articles/Instapaper-Matter-Archive" \
  .venv/bin/python site/generate.py --out _site

# 2. Create the Pages project and deploy (wrangler lives under volta)
wrangler pages project create reading-adamthede --production-branch main
wrangler pages deploy _site --project-name reading-adamthede --branch main

# 3. Custom domain: Cloudflare dashboard -> Pages -> reading-adamthede ->
#    Custom domains -> add reading.adamthede.com (the adamthede.com zone is
#    already on Cloudflare, so this is one click + automatic CNAME).
```

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
