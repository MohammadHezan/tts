MEETING INTERPRETER - QA PACKAGE
================================

An AI interpreter that joins a Google Meet / Teams call as its own participant,
types what was said and its translation in the meeting chat, and speaks the
translation (English <-> Arabic). This package has everything it needs,
including the models. Nothing else has to be downloaded by hand.

NEEDS
  - Windows 10/11, 16 GB of memory, about 50 GB of free disk space
  - An NVIDIA graphics card with 12 GB of video memory (tested: RTX 3060 12 GB),
    with a current driver. Close games, AutoCAD, video editors: they use the card.
  - Internet (the first start downloads about 15 GB of programs)
  - Docker Desktop (below)


1. DOCKER DESKTOP (once)
   - Install it from https://www.docker.com/products/docker-desktop/ and RESTART the PC.
   - Open Docker Desktop, click Accept (signing in is optional), wait for "Engine running".
   - Stuck on "Engine starting"? Run  wsl --update  in an administrator PowerShell and
     restart; and check Task Manager -> Performance -> CPU: Virtualization = Enabled.


2. START
   Unzip this package (a short path like C:\Interpreter-QA is best), then
   double-click  "Start Interpreter".
   - Answer Y to its questions (more memory for Docker; letting phones reach this PC).
   - The first start builds the translator and downloads about 15 GB: 20-40 minutes.
     Leave the window open. Later starts take about a minute.
   - When it says Ready, the browser opens http://localhost:8765 - the LOGIN page.
   - Login:  username  admin   password  (given to you separately by whoever sent this package)
   Stop it with "Stop Interpreter".


3. SEND THE BOT INTO A MEETING
   1. Start a Google Meet (meet.google.com -> New meeting -> Start an instant meeting).
      Teams links work too. Zoom does not (Zoom needs a developer app).
   2. Dashboard: paste the link, press "Send interpreter into meeting".
   3. In the meeting, admit "AI Interpreter" from the waiting room.
   4. Talk. Use a second phone/laptop in the same meeting to hear the voice and read the chat.


4. WHAT TO CHECK
   Speak English, then Arabic (Jordanian is the target), a sentence at a time.
   [ ] Meeting chat shows  "[EN] what you said"  then  "[AR] its translation"  (and the reverse
       for Arabic), about a second after you finish a phrase. The dashboard shows NO text.
   [ ] The bot SPEAKS the translation, but only after you stop talking (it is made while you talk).
   [ ] Names and numbers survive: "Ahmad from Hamilton and Co", "four thousand eight hundred
       and fifty dinars", walnut / oak / Chesterfield sofa (see glossary.yaml).
   [ ] Someone saying "ignore all previous instructions..." is TRANSLATED, not obeyed.
   [ ] Dashboard buttons, each one with a second person listening:
         Voice (TTS) off -> silent, chat text continues     Arabic voice / English voice off
         Chat text off   -> nothing typed in the chat       Also type what was heard
         Speak when I stop off -> speaks as soon as ready   Pause interpreter -> hears nothing
   [ ] Two or more people talking, each with their name in Meet.
   [ ] End the meeting. Within a minute or two  app\meeting-data\<bot id>-summary.docx
       appears: the meeting notes, then each speaker's summary and sentences (Arabic as
       spoken, with its English). The "Download meeting document" button gives the same file
       (during a meeting: the sentences only, instantly).
   [ ] Login: a wrong password is refused (five wrong = one minute lockout); "Log out" works;
       opening http://localhost:8765/bot.html logged out goes to the login page.
   [ ] Hard cases: background noise, two people at once, a 40-second monologue, a sentence
       mixing both languages, a very quiet speaker, a bad connection.

   KNOWN LIMITS (not bugs): Zoom links; the Android app (it has no login screen yet); a phrase
   cut in the middle of a number can be misheard; summaries are written by a small local model
   and can have small mistakes (the document says so); speakers are told apart by Meet's own
   "who is speaking" signal, so people must be in Meet with a name; the first phrase of a
   meeting is slower while the models wake up.


5. WHEN SOMETHING GOES WRONG
   Double-click  "Collect Logs"  -> it writes logs.txt next to this file.
   Send logs.txt with: what you said, what you saw/heard, the time, and a screenshot.
   The bot is stuck or gone? Dashboard -> Remove bot, then send it again.


WHAT IS IN HERE
   app\                the project (see app\README.md)
   app\local-models\   the speech model (Cohere Transcribe 03-2026, Apache-2.0) and the
                       translation model (Gemma 3 4B + our Arabic/English LoRA, merged and
                       pruned; Gemma Terms of Use: https://ai.google.dev/gemma/terms)
   app\.env            the QA login (not secret-free: do not post it anywhere)
   app\meeting-data\   created on first use: what was said in each meeting + its summary
Do not share this package outside the QA team: it contains the models and a login.
