# AutoMection

## Local run (एक कमांड)

```bat
run.bat
```

या:

```bat
python start.py
```

Browser खुलेगा: `http://127.0.0.1:8000/settings`  
**Start Bot** → Chrome new tab में game login/automation।

## Config (`.env`)

सभी URLs / DB यहीं से:

| Key | Example |
|-----|---------|
| `APP_URL` | `http://127.0.0.1:8000` |
| `GAME_SITE` | `damanvipgames.com` |
| `GAME_LOGIN_URL` | full login URL |
| `GAME_WINGO_URL` | full Win Go URL |
| `DB_*` | MySQL |

`.env.example` कॉपी करके `.env` बनाओ। कल URL बदलना हो तो सिर्फ `.env` अपडेट करो।
