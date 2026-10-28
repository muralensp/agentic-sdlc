Find the cause before changing code.

1. Reproduce the failure with the smallest command or test you can. If you cannot reproduce it, say so.
2. Minimise it: remove everything that is not needed to make it fail.
3. List hypotheses, most likely first. Test one at a time by adding output or a probe, not by guessing a fix.
4. When the cause is confirmed, fix it at the cause, not the symptom.
5. Add a regression test that fails without the fix and passes with it.
6. If you are stuck, write a short report of what you tried and what you ruled out instead of trying more of the same.
