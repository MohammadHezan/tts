MEETING INTERPRETER - FOR YOUR COMPUTER
=======================================

An AI interpreter that joins your Google Meet / Teams call as its own
participant, hears everyone, and speaks each sentence in the other language
(English <-> Arabic) so the whole call hears it.

NEEDS
  - A Windows or Linux PC with 16 GB of memory (Mac works too)
  - Docker Desktop (Windows/Mac) or Docker (Linux)
  - About 20 GB of free disk space

START
  Windows:  double-click  "Start Interpreter"
            (the first time it may ask two questions - answer Y to both)
  Linux:    ./start.sh
  Mac:      ./start.sh

  The first start downloads about 15 GB and can take 20-40 minutes.
  After that it starts in about a minute. When it's ready, your browser
  opens the interpreter page.

USE IT
  1. Start a Google Meet (on your phone or computer) and copy its link.
  2. On your phone: Interpreter app -> Meeting Bot. It finds this
     computer by itself (same Wi-Fi). Or use the page on this computer.
  3. Paste the link and tap "Send interpreter into meeting".
  4. In Meet, let "AI Interpreter" in.
  5. Talk. After each sentence, the interpreter says it in the other
     language (about 10-15 seconds later on a computer without a
     graphics card).

STOP
  Windows:  double-click  "Stop Interpreter"
  Linux/Mac: ./stop.sh

TIPS
  - Google Meet and Microsoft Teams work as they are. Zoom needs extra
    Zoom developer setup - use Meet for testing.
  - Testing with two phones in one room? Use earbuds, or put the phones in
    different rooms, or they will hear each other.
