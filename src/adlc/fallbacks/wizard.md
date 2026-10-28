Turn a step only a person can do into an interactive script. A person must be present.

1. Write the script to .adlc/wizards/<name>.sh. It only runs when the person runs it.
2. Walk through one step at a time: say what to do, wait for the person, check the result, and stop with a clear message if a check fails.
3. Never read, print or store secrets; let the person paste them straight into the tool that needs them.
4. Make it safe to re-run: skip steps that are already done.
5. Print what was done and the suggested next step at the end.
