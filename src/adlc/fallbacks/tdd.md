Work test first, one behaviour at a time.

1. Pick the next single behaviour from the acceptance criteria.
2. Write one test for it. Run it and see it fail for the right reason. Never trust a test you have not seen fail.
3. Write the least code that makes it pass. Run the tests and read the real result.
4. Refactor only while everything is green, then run the tests again.
5. Test through public interfaces. Do not change a test just to make it pass.
6. Stop after one behaviour per iteration; the orchestrator decides whether the work is done, not you.
