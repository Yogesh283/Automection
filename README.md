# AutoMection

## Local run (one command)

```bat
run.bat
```

or:

```bat
python start.py
```

Browser opens: `http://127.0.0.1:8000/settings`  
**Start Bot** -> game login/automation in a Chrome new tab.

## Config (`.env`)

All URLs / DB settings come from here:

| Key | Example |
|-----|---------|
| `APP_URL` | `http://127.0.0.1:8000` |
| `GAME_SITE` | `damanvipgames.com` |
| `GAME_LOGIN_URL` | full login URL |
| `GAME_WINGO_URL` | full Win Go URL |
| `DB_*` | MySQL |

Copy `.env.example` to `.env`. To change the game URL later, update `.env` only.
