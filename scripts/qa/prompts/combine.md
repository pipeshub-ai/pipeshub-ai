# Resolve a conflict between pull requests

Several pull requests are being stacked on the main branch so they can be tested together. The one named below conflicts with the ones stacked before it. The files listed contain git conflict markers: the block between `<<<<<<<` and `=======` is the combined side (main plus the earlier pull requests), the block between `=======` and `>>>>>>>` is this pull request's side.

Resolve every conflict so the result keeps the intent of both sides:

- Read both changes first (the commands below show each side's diff), and the code around them, so you know what each side meant.
- Keep both behaviours when they can live together, for example two new entries in a list, or two independent checks.
- When they really contradict, keep the combined side and add nothing invented; the test run will show the rest.
- Do not refactor, reformat or fix anything else. Edit only the files listed, and remove every marker line.

You can read and search the repository and run `git diff`, `git log` and `git show`. When every listed file is free of markers, stop. Reply with one sentence per file saying what you kept.
