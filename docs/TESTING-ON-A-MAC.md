# Testing on a Mac — step by step

Written for someone who has never used Terminal. About 15 minutes, most of it
waiting. Nothing here changes anything on your Mac outside the project folder.

## 1. Install Python (once)

1. Go to <https://www.python.org/downloads/>
2. Click the yellow **Download Python** button
3. Open the downloaded file, then click **Continue → Continue → Agree → Install**
   (it asks for your Mac password — that's normal)
4. You should see *"The installation was successful."*

Already have Python? The launcher in step 5 will tell you, and you can skip this.

## 2. Download the project

1. Open <https://github.com/sarahnagaty-beep/bot_prototype/tree/claude/buyer-whatsapp-ai-bot-7l201r>
2. Click the green **Code** button → **Download ZIP**
3. Open your **Downloads** folder and double-click the ZIP — a folder called
   `bot_prototype-claude-buyer-whatsapp-ai-bot-7l201r` appears
4. Drag that folder onto your **Desktop**

## 3. Open Terminal

Press **⌘ Command + Space**, type `Terminal`, press **Enter**. A plain window opens
where you type a line and press Enter. That's all it is.

## 4. Point Terminal at the folder

Type this — including the space at the end — but **don't press Enter yet**:

```
cd 
```

Now **drag the folder from your Desktop into the Terminal window**. It fills in the
address for you. Press **Enter**.

## 5. Start it

```
bash start.command
```

The first time, this takes a minute or two and prints a lot of scrolling text —
that's normal. It ends with a menu.

## 6. Test

| Menu option | What it does |
|---|---|
| **1** | Talk to the bot as if you were a buyer |
| **2** | Open the dashboard — the leads and briefs the bot produced |
| **3** | Read a finished sample conversation |
| **4** | Clear your test data and start fresh |
| **5** | Quit |

Start with **1**. Answer as a real buyer would — full sentences are fine, you don't
have to use the exact words shown. When the conversation ends, choose **2** and open
your own name in the lead list: that is the brief a consultant would have received
before calling you.

The test cases to work through are in [UAT.md](UAT.md).

## Stopping and coming back

- **Stop the dashboard:** press **Control + C** (the `control` key, not `command`)
- **Leave entirely:** choose **5**, then close the Terminal window
- **Come back later:** repeat steps 3–5 (the setup doesn't run twice)

## If something goes wrong

| What you see | What to do |
|---|---|
| `command not found: python3` | Python isn't installed — do step 1 |
| `no such file or directory` | The `cd` line didn't land. Redo step 4, dragging the folder in rather than typing the name |
| `permission denied` | Type `bash start.command` — with the word `bash` in front |
| The browser doesn't open | Type `localhost:8000` into Chrome or Safari yourself |
| *"This site can't be reached"* | The Terminal window must stay open while the dashboard runs |
| Terminal seems frozen | It's usually waiting for you. Press **Enter**. To interrupt anything, **Control + C** |

Nothing you type can damage the project. If it gets into a strange state, close the
Terminal window, open a new one, and start again from step 4.
