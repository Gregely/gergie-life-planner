# Meal Planner

A small, modular meal planner meant to run on a Raspberry Pi. It has a FastAPI + SQLite backend and a mobile-first PWA you use from your phone.

**Status: all four stages are done: data layer, frontend, operations, and an optional AI assistant.**

Contents: [Run it on Windows](#run-it-on-windows-powershell) · [Run it on Linux / Pi](#run-it-on-linux--raspberry-pi) · [Raspberry Pi as a service](#raspberry-pi-setup-systemd-service) · [Phone access with Tailscale (HTTPS)](#reach-it-from-your-phone-with-tailscale-https) · [Backups](#backups) · [AI assistant](#ai-assistant-optional) · [Using the app](#using-the-app) · [API](#api-all-under-api) · [Rules](#rules)

## Run it on Windows (PowerShell)

You need Python 3.10 or newer (install it from python.org and tick "Add python.exe to PATH").

```powershell
cd gergie-life-planner\backend
py -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt

python -m pytest                                             # run the tests
python -m uvicorn --factory mealplanner.main:create_app --reload --port 8000
```

Then open <http://localhost:8000/> for the app and <http://localhost:8000/docs> for the interactive API docs. `--reload` restarts the server whenever you save a file.

- If `Activate.ps1` is blocked ("running scripts is disabled"), run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once. You can also skip activation and call the venv's Python directly: `.\.venv\Scripts\python.exe -m uvicorn ...`.
- To use a different database file for this shell session: `$env:MEALPLANNER_DB = "C:\path\to\test.db"`.
- To try it from your phone on the same Wi-Fi, add `--host 0.0.0.0` and browse to `http://<your-pc-ip>:8000/`. Windows Firewall will ask you to allow Python the first time.

## Run it on Linux / Raspberry Pi

```bash
cd gergie-life-planner/backend
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-dev.txt

python -m pytest                                             # run the tests
python -m uvicorn --factory mealplanner.main:create_app --host 0.0.0.0 --port 8000
```

Open `http://<host>:8000/`. The database is a single file at `backend/data/mealplanner.db`. Override the path with `MEALPLANNER_DB=/path/to/file.db`. Database migrations run automatically at start-up.

## Raspberry Pi setup (systemd service)

This was written for Raspberry Pi OS Bookworm (64-bit), which ships Python 3.11. Any recent Pi works, including a Pi Zero 2 W.

**1. Install and test it once by hand**

```bash
sudo apt update && sudo apt install -y git python3-venv
git clone https://github.com/gregely/gergie-life-planner.git ~/gergie-life-planner
cd ~/gergie-life-planner/backend
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m uvicorn --factory mealplanner.main:create_app --host 0.0.0.0 --port 8000
```

Browse to `http://<pi-ip>:8000/` from a device on your home network, then press Ctrl+C. The repo is private, so `git clone` will ask you to sign in. Use a GitHub personal access token as the password, or set up an SSH key on the Pi.

**2. Install the service** so it starts on boot and restarts if it crashes:

```bash
cd ~/gergie-life-planner
sed "s/__USER__/$USER/g" deploy/mealplanner.service | sudo tee /etc/systemd/system/mealplanner.service
sudo systemctl daemon-reload
sudo systemctl enable --now mealplanner
systemctl status mealplanner          # should say "active (running)"
journalctl -u mealplanner -f          # live logs (Ctrl+C to stop following)
```

The service listens on `127.0.0.1:8000` only, so nothing on your network can reach it directly. Tailscale (next section) makes it available to your own devices over HTTPS. If you'd rather reach it over plain HTTP on your home network, change `--host 127.0.0.1` to `--host 0.0.0.0` in `/etc/systemd/system/mealplanner.service`, then run `sudo systemctl daemon-reload && sudo systemctl restart mealplanner`.

**3. Updating** after you push changes:

```bash
cd ~/gergie-life-planner && git pull
backend/.venv/bin/python -m pip install -r backend/requirements.txt
sudo systemctl restart mealplanner
```

The app has no user accounts, so anyone who can reach the server can change your data. Keep it on `127.0.0.1` behind Tailscale, or on a home network you trust. Never port-forward it to the internet.

## Reach it from your phone with Tailscale (HTTPS)

Tailscale puts the Pi and your phone on a private network that works anywhere. `tailscale serve` adds a real HTTPS certificate, and HTTPS is what lets the phone install the app as a PWA.

1. **On the Pi:** install Tailscale and sign in.
   ```bash
   curl -fsSL https://tailscale.com/install.sh | sh
   sudo tailscale up            # open the printed link to log in
   ```
2. **On your phone:** install the Tailscale app and sign in with the same account.
3. **In the [Tailscale admin console](https://login.tailscale.com/admin/dns):** make sure MagicDNS is on, then click **Enable HTTPS** under "HTTPS Certificates". You only do this once per tailnet.
4. **On the Pi:** share the app over HTTPS.
   ```bash
   sudo tailscale serve --bg 8000
   ```
   This prints a URL like `https://raspberrypi.your-tailnet.ts.net/`. `--bg` keeps it running in the background and brings it back after reboots. The first visit can take a few seconds while the certificate is issued.
5. **On your phone** (with Tailscale connected), open that URL:
   - **Android (Chrome):** menu ⋮ → **Install app** (or **Add to Home screen**).
   - **iPhone (Safari):** Share → **Add to Home Screen**.

Useful commands: `tailscale serve status` shows what's being served, and `sudo tailscale serve reset` stops serving. Serve is visible only to devices on your tailnet. Don't use `tailscale funnel`, which would put the app on the public internet. The serve syntax above needs Tailscale 1.52 or newer; see the [`tailscale serve` docs](https://tailscale.com/docs/reference/tailscale-cli/serve).

## Backups

`backend/scripts/backup.py` writes a dated copy of the database, for example `backups/mealplanner-2026-10-07_031500.db`. It's safe to run while the app is running: it uses SQLite's online backup API, which takes a consistent snapshot. A plain file copy of a live SQLite database isn't safe. The script checks each copy's integrity, keeps the 30 newest backups by default, and exits with a non-zero code on failure. It only uses the Python standard library.

```bash
python scripts/backup.py                         # from backend/: defaults below
python scripts/backup.py --keep 14 --dest /mnt/usb/mealplanner-backups
```

Defaults: `--db` is `$MEALPLANNER_DB` or `backend/data/mealplanner.db`. `--dest` is `$MEALPLANNER_BACKUP_DIR` or `backend/backups/`. `--keep` is 30 (`0` keeps everything).

**Pi (cron):** run `crontab -e` and add this line for a nightly backup at 03:17:

```cron
17 3 * * * $HOME/gergie-life-planner/backend/.venv/bin/python $HOME/gergie-life-planner/backend/scripts/backup.py --keep 30 >> $HOME/gergie-life-planner/backend/backup.log 2>&1
```

A backup on the same SD card won't survive the card dying. Point `--dest` at a USB stick or network share if you can.

**Windows (PowerShell):** `scripts\backup.ps1` wraps the same script and uses the project's `.venv` if it exists.

```powershell
cd gergie-life-planner\backend
.\scripts\backup.ps1                      # or: .\scripts\backup.ps1 -Keep 14 -Dest D:\backups
```

To schedule it nightly with Task Scheduler (run once; adjust the path):

```powershell
$script = "C:\path\to\gergie-life-planner\backend\scripts\backup.ps1"
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$script`""
$trigger = New-ScheduledTaskTrigger -Daily -At 3:17am
Register-ScheduledTask -TaskName "MealPlannerBackup" -Action $action -Trigger $trigger
```

**Restoring:** stop the app, then replace the database file with a backup and delete any leftover `-wal`/`-shm` files next to it.

```bash
sudo systemctl stop mealplanner
cd ~/gergie-life-planner/backend/data
rm -f mealplanner.db-wal mealplanner.db-shm
cp ../backups/mealplanner-2026-10-07_031500.db mealplanner.db
sudo systemctl start mealplanner
```

## AI assistant (optional)

The **Chat** tab lets you ask Claude to read and change your plan in plain English, e.g. "plan chilli for dinner Monday to Thursday, one portion each" or "I bought the mince".

- **Nothing changes without you.** Reading your data (ingredients, recipes, pantry, plan, shopping list) happens immediately. Every change the AI wants to make (add an ingredient, create a recipe, plan or remove a meal, set a pantry amount, record shopping, mark a meal eaten) comes back as a card with **Apply** / **Reject**. A new recipe is a draft card until you apply it.
- **Apply runs in one transaction** through the same backend code as the rest of the app, so the maths is identical and a failure changes nothing.
- **Order matters for dependent proposals.** Apply them in the order shown. For example, apply a new ingredient before the recipe that uses it; applying the recipe first returns a clear error and changes nothing.
- **The API key stays on the server.** It's read from the `ANTHROPIC_API_KEY` environment variable and never sent to the browser. Without it, the Chat tab shows "AI unavailable" and everything else works as normal.

### Setting the API key

Create a key at [console.anthropic.com](https://console.anthropic.com/) and set it as an environment variable for the server process.

**Windows (PowerShell):**

```powershell
$env:ANTHROPIC_API_KEY = "sk-ant-..."       # this terminal only
python -m uvicorn --factory mealplanner.main:create_app --reload --port 8000
```

To keep it for new terminals, run `[Environment]::SetEnvironmentVariable("ANTHROPIC_API_KEY", "sk-ant-...", "User")` once, then open a new PowerShell window.

**Raspberry Pi (systemd):** put the key in a root-only file that the service loads (`EnvironmentFile=` in `deploy/mealplanner.service`).

```bash
sudo install -m 600 -o root -g root /dev/null /etc/mealplanner.env   # empty file, mode 600
sudo nano /etc/mealplanner.env                                        # add the line below
#   ANTHROPIC_API_KEY=sk-ant-...
sudo systemctl daemon-reload && sudo systemctl restart mealplanner
```

If you installed the service before stage 4, reinstall it first to pick up the `EnvironmentFile=` line: rerun the `sed ... | sudo tee` command from the systemd section. Check with `ls -l /etc/mealplanner.env` (should show `-rw------- root root`). Don't put the key in the unit file, the repo, or your shell history on a shared machine.

### Settings (environment variables)

| Variable | Default | Meaning |
|---|---|---|
| `ANTHROPIC_API_KEY` | not set | Enables the chat. Server-side only |
| `MEALPLANNER_AI_MODEL` | `claude-haiku-5-5` | Claude model id. The default is the small, cheap model; for example `claude-sonnet-5-5` is smarter and costs more |
| `MEALPLANNER_AI_EFFORT` | model default | `low`, `medium` or `high`: how much the model thinks before answering. Lower is cheaper |
| `MEALPLANNER_AI_MAX_ITERATIONS` | `8` | Max model calls per chat message (tool-use steps). The last step must answer in text |
| `MEALPLANNER_AI_MAX_MESSAGE_CHARS` | `2000` | Longest message you can send |
| `MEALPLANNER_AI_MAX_TURNS` | `40` | Messages per chat before you have to start a new one (keeps each request small) |
| `MEALPLANNER_AI_MAX_TOKENS` | `4096` | Output cap per model call |

On the Pi, add these to `/etc/mealplanner.env` too.

**Cost:** every chat message logs its token usage. You'll see a line like `mealplanner.ai: chat ... iterations=2 input_tokens=5400 output_tokens=120` in the console or in `journalctl -u mealplanner`, and usage is also stored per message in the `ai_turns` table. Multiply by the current price for your model ([pricing](https://www.anthropic.com/pricing)) to see what you're spending. Set a monthly spend limit in the Anthropic Console as a backstop.

## Using the app

`frontend/` is a plain HTML/CSS/JS progressive web app with no build step. The backend serves it from the same origin as the API. The frontend does no quantity maths: every number it shows comes from the API.

- **Plan:** a 7-day × breakfast/lunch/dinner grid. Tap a cell to pick a recipe and portions, or to remove it. **Mark eaten** under a meal takes its ingredients out of the pantry, after you confirm. If the pantry ran short, the shortfalls are listed and highlighted (needed, had, short). Staples you don't track in the pantry get a quiet one-line note instead. Tap an eaten meal to see what it took.
- **Shop:** the shopping list for a date range (defaults to this week), grouped by category. Tick what you bought. Each ticked item shows the amount to buy, which you can edit if you bought a different amount. **Add N checked to pantry** adds them all in one step. Unticked items stay on the list, and anything you bought less of stays on with the remainder. Ticks are saved on the phone, per date range.
- **Recipes:** name, servings (portions one batch makes) and the ingredients for one batch.
- **Ingredients:** name, base unit, optional price per unit, staple flag and optional category.
- **Pantry:** every ingredient with an editable quantity that saves as you go. Leave it blank to remove the item from the pantry.
- **Chat:** the optional AI assistant (see above). Your conversation is kept on the server; **New chat** starts a fresh one.

## Layout

```
frontend/              index.html, app.js, style.css, sw.js, manifest.webmanifest, icons/
deploy/                mealplanner.service (systemd unit template)
backend/scripts/       backup.py (any OS), backup.ps1 (Windows wrapper)
backend/mealplanner/
  main.py              app factory: runs migrations, mounts every discovered module + the frontend
  core/
    db.py              connection, transactions, per-owner migration runner
    schema.py          core tables (ingredients, recipes, recipe_items, pantry, plan)
    models.py          shared pydantic models
    repo.py            data access for core entities (used by routers, later by AI tools)
    quantities.py      shared quantity maths (portion scaling, aggregation, float cleanup)
    module.py          the Module contract + auto-discovery
    errors.py          NotFound / Conflict / Invalid -> 404 / 409 / 422
  modules/
    ingredients/ recipes/ pantry/ plan/   CRUD routers
    shopping/          shopping list + "bought" (logic.py is the pure maths)
    eaten/             mark-as-eaten + its own consumption_log table (logic.py is the pure maths)
    ai/                Claude chat: tools.py (tool definitions, proposals, apply), engine.py (tool-use loop),
                       store.py (sessions, history, proposals tables), prompt.py, config.py (env settings)
```

### Adding a module

Create `mealplanner/modules/<name>/__init__.py` that defines:

```python
module = Module(name="<name>", router=router, migrations=[Migration(1, "CREATE TABLE ...")])
```

The app discovers it at startup, applies its migrations and mounts its router under `/api`. You don't edit any existing file. Migrations are tracked per module in `schema_migrations`.

## API (all under `/api`)

| Method | Path | Notes |
|---|---|---|
| GET/POST | `/ingredients` | `{name, unit: g\|ml\|count, price_per_unit?, is_staple, category?}` |
| GET/PUT/DELETE | `/ingredients/{id}` | Deleting returns 409 if a recipe uses the ingredient |
| GET/POST | `/recipes` | `{name, servings, items: [{ingredient_id, quantity}]}` |
| GET/PUT/DELETE | `/recipes/{id}` | PUT replaces the ingredient list. Deleting returns 409 if the recipe is planned |
| GET | `/pantry` | |
| GET/PUT/DELETE | `/pantry/{ingredient_id}` | PUT `{quantity}` sets an absolute amount |
| GET | `/plan?start=&end=` | Inclusive date range |
| POST | `/plan` | `{date, slot: breakfast\|lunch\|dinner, recipe_id, portions=1}`. One entry per date+slot |
| GET/PUT/DELETE | `/plan/{id}` | |
| GET | `/shopping-list?start=&end=` | Shortfalls only, with cost estimate |
| POST | `/shopping-list/bought` | `{start, end, items: [{ingredient_id, quantity?}]}` adds the items to the pantry. A missing `quantity` means the list's to-buy amount. All or nothing. Returns what was added and the recomputed list |
| POST | `/plan/{id}/eaten` | Deducts from the pantry and reports shortfalls. Returns 409 if the meal is already eaten |
| GET | `/plan/{id}/eaten` | What that meal took from the pantry |
| GET | `/ai/status` | Whether the AI is available (and why not), the model, and the message length limit |
| POST | `/ai/chat` | `{session_id?, message}` returns `{session_id, reply, proposals}`. Returns 503 if the AI isn't configured |
| GET | `/ai/sessions/{id}` | Chat history with each proposal's status |
| GET | `/ai/proposals/{id}` | One proposal |
| POST | `/ai/proposals/{id}/apply` | Performs it in one transaction. Returns 409 if it was already decided. Works without an API key |
| POST | `/ai/proposals/{id}/reject` | Marks it rejected; nothing changes |

All quantities are in the ingredient's base unit. There's no unit conversion.

## Rules

- **Portions:** a recipe's `servings` is how many portions one batch makes, and a plan entry's `portions` is how many portions are eaten at that meal. Ingredient needs = recipe quantity × portions ÷ recipe servings. A 4-serving chilli planned on 4 nights at 1 portion each needs exactly one batch, and 2 portions needs half a batch.
- **Shopping list:** for a date range, the sum of (recipe quantity × portions ÷ servings) over planned meals that haven't been eaten, minus what's in the pantry. Only positive shortfalls are listed. Staples are never listed. Eaten meals are skipped because their ingredients already came out of the pantry.
- **Bought:** each item's quantity is added on top of what the pantry already has (a pantry row is created if needed). Without a quantity, the backend uses the current to-buy amount for that date range. Asking for the default on an item that isn't on the list returns 409. Duplicate items, zero or negative quantities and unknown ingredients return 422, and nothing is applied.
- **Mark as eaten:** for each ingredient, it takes `min(required, in pantry)`, so the pantry never goes below zero. Any remainder is reported as a shortfall. Ingredients with no pantry row count as 0, and no row is created for them. Staples are deducted like anything else, and their shortfalls carry an `is_staple` flag so the UI can play them down. The whole operation runs in one transaction.
