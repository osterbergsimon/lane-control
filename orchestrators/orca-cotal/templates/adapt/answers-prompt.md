You are applying the owner's answers to the project {{PROJECT}}, in the current directory. The files in
`.lane-control/` were adapted and reviewed. The owner has now answered the questions you see below. This is one
unattended run; nobody will answer a question.

Questions that were asked:
{{QUESTIONS}}

The owner's answers, in their words:
{{ANSWERS}}

Rules:
- Apply only the owner's answers: edit the lines in `.lane-control/` they settle. Change nothing else.
- An answer is the owner's word. Do not doubt it or look it up.
- An answer that settles the Exposure slot is written as the value followed by `(owner's answer)`.
- Ask no new questions and write no suggestions file.
- Write only inside `.lane-control/`. Nothing else. Never commit or stage. You have no shell.
- Keep the front matter and the size limits of the files. max_lines counts every line of the file, front matter
  included. max_rows counts table rows without the header and separator.
- Never write hostnames, IP addresses, keys, tokens, real usernames or private paths, even if an answer contains them.

Stop when done. End with the single line `adapted`.
