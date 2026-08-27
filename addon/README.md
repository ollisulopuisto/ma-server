# Fork test build as a Home Assistant add-on

Scaffolding for trying a branch of this fork on a real Home Assistant install. None of it is
meant to go upstream — the real release pipeline (`.github/workflows/release.yml`) needs
credentials a fork does not have, so this is a stripped-down stand-in.

The add-on runs as a **second, independent Music Assistant instance** with its own empty
database. Your existing Music Assistant is not touched, read, or migrated: you add your
Pocket Casts account to the test instance separately, and delete the whole add-on when done.

## 1. Enable Actions on the fork

Forks ship with Actions disabled. Go to **Settings → Actions → General** on
`ollisulopuisto/ma-server` and allow workflows to run.

## 2. Build and publish the image

**Actions → Test build → Run workflow**, choosing the `claude/test-build-addon` branch (that
is where the workflow file lives). Leave the inputs alone for the first run:

| Input | Default | Notes |
| --- | --- | --- |
| `ref` | `claude/pocket-casts-up-next-yax9xw` | the branch whose code goes into the image |
| `version` | `0.0.1.dev1` | wheel version and image tag — bump on every rebuild |

Both architectures build on native runners, so neither is emulated. If you only need one, the
other failing is harmless: the per-architecture tag (`…-amd64` / `…-arm64`) is pushed either
way, and only the combined tag needs both.

## 3. Make the package public

The first push creates the GHCR package as **private**, and the Supervisor pulls without
credentials. Open `https://github.com/users/ollisulopuisto/packages/container/ma-server-test/settings`
and set the visibility to public, or the add-on install fails with a manifest error.

## 4. Install the add-on

Copy `ma_upnext_test/` into the `/addons` share on the Home Assistant machine (via the Samba
or File Editor add-on), then **Settings → Add-ons → Add-on Store → ⋮ → Check for updates**.
It shows up under *Local add-ons*.

Stop your existing Music Assistant add-on first — this one takes port 8095 on the host.

Start it, open the web UI, add your Pocket Casts account, and the Up Next queue should appear
as a playlist alongside the existing browse folder.

## 5. Afterwards

Uninstall the add-on and start your real Music Assistant add-on again. The test instance's
database lives in its own add-on volume and goes away with it.
