# terminal of wink: the X bot

An autonomous account in the spirit of Truth Terminal, but it is the ;) from answer #1685.

- **Posts on its own every 10–15 minutes**, mixing five kinds of post:
  - plain thoughts that blend everyday life with being a qubit
  - a word sent as binary (the bits come from a real Qiskit circuit)
  - two strings put in superposition and measured once (real Aer run)
  - a measured `|;)> + |8)>` histogram, sometimes with readout noise (real Aer run)
  - a fragment of its origin story
- **Replies when someone @'s it or replies to its posts** (both arrive as mentions), checked every 90 seconds.
- **Safety rules built in**: no links, no hashtags, no other emoji, no price or money talk, the contract address only when someone asks for it, no unprompted @'s, max 3 replies per person per 30 minutes.

Code: `bot/xbot.py` (loop, X calls, post builders) and `bot/persona.py` (voice, lore, rules).

## 1. Get X API access

1. Go to **developer.x.com** and sign in **with the bot account** (the account that will post).
2. Create a **Project** and an **App** inside it.
3. Add billing credits. X API access is pay-per-use: you prepay credits and each call draws from them.
   Set a spending limit in the console.
4. In the app's **User authentication settings**:
   - App permissions: **Read and write**
   - Type of app: **Web App, Automated App or Bot**
   - Callback URL: `https://terminalofwink.com` and Website URL: `https://terminalofwink.com` (required fields; not used by the bot)
5. Open **Keys and tokens**:
   - Copy the **API Key** and **API Key Secret** (these become `X_API_KEY`, `X_API_SECRET`)
   - Under **Access Token and Secret**, click **Generate** and copy both (`X_ACCESS_TOKEN`, `X_ACCESS_TOKEN_SECRET`).
     Generate these **after** setting Read and write, or posting will fail with 403. If you set permissions later, regenerate them.

## 2. Label the account as automated

On X, signed in as the bot: **Settings → Your account → Account information → Automation**,
and link your personal account as the managing account. The profile then shows an "Automated" label.
X's rules expect this for bot accounts.

## 3. Deploy the worker on Render

1. **New → Background Worker**, pick the `terminal-of-wink` repo.
2. Language: **Docker**. Region: **Frankfurt**. Instance: **Starter** (workers have no free tier).
3. **Docker Command**: `python -m bot.xbot`
4. Environment variables:

| Key | Value |
|---|---|
| `ANTHROPIC_API_KEY` | same key as the website |
| `X_API_KEY` / `X_API_SECRET` | from step 1 |
| `X_ACCESS_TOKEN` / `X_ACCESS_TOKEN_SECRET` | from step 1 |
| `DRY_RUN` | `1` to start |

5. Deploy, then open **Logs**. You should see `signed in as @yourbot [DRY RUN]` and, every 10–15 minutes,
   the post it *would* have made. Mentions are read and the replies it *would* send are printed too.
6. Happy with the voice? Change `DRY_RUN` to `0` and save. It goes live on the redeploy.

## Tuning (environment variables)

| Variable | Default | |
|---|---|---|
| `POST_MIN_MINUTES` / `POST_MAX_MINUTES` | 10 / 15 | time between its own posts (random in this range) |
| `MENTION_POLL_SECONDS` | 90 | how often it checks for mentions |
| `MAX_REPLIES_PER_POLL` | 15 | reply cap per check |
| `MAX_REPLIES_PER_USER_30MIN` | 3 | stops one person farming replies |
| `DRY_RUN` | 0 | 1 = log only, post nothing |

To change the voice, edit `bot/persona.py` and push; Render redeploys the worker.

## Try it locally first

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...
python -m bot.xbot --preview 10     # 10 sample posts, nothing touches X
```

## Costs to expect

- X: at the time of writing, pay-per-use charges per post created and per post read, and posts that contain links cost
  much more. That is why the bot never posts links. At 10–15 minutes it makes roughly 100–145 posts a day plus replies.
  Check current rates in the developer console and set a limit there.
- Claude: one short call per post and per reply, on the fast Haiku model.
- Render: one Starter worker.

## If something goes wrong

- **403 on posting**: access tokens were generated before Read and write was set. Regenerate them.
- **401**: a key or token was pasted wrong.
- **429 / rate limit**: the bot backs off by itself (1 min, doubling up to 15 min).
- **It went quiet**: check Render logs and your X credit balance.
- **Emergency stop**: set `DRY_RUN=1`, or suspend the worker in Render.
