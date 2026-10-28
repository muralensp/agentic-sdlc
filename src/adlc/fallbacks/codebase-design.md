Shape modules so they are easy to use, test and navigate.

1. Prefer deep modules: a small interface that hides a lot of behaviour.
2. Put each boundary (seam) where behaviour can be replaced in a test, such as at I/O, time, the network and other processes.
3. Test through the interface, not through internals.
4. Name modules and functions with the project's own terms from the glossary.
5. Record the interface and boundary choices in the brief or an ADR before writing the implementation.
