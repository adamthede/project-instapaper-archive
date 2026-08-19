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
