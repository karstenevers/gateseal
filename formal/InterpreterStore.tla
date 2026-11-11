--------------------------- MODULE InterpreterStore ---------------------------
(***************************************************************************)
(* The machine-wide interpreter store, which PreparedAttempts              *)
(* instantiates. Another repository's activation runs the same acquire on  *)
(* the same entry, and its attempts' links keep the entry as this          *)
(* worktree's do. Its acquire changes the entry only when the entry is not *)
(* usable, so the other repository appears as that install. collect runs   *)
(* at the end of every activation in any repository, this worktree's       *)
(* included, so Collect may run at any time. These actions leave the       *)
(* worktree's variables to PreparedAttempts, which keeps them unchanged.   *)
(***************************************************************************)

CONSTANTS StoreLock, SealChecked, LiveWhileDir

VARIABLES tree, sealed, refs, holder, fpc, gpc, doomed, dirs, recorded

\* store.lock is free for w to take, or the design takes no lock.
Free == ~StoreLock \/ holder = "none"
Take(w) == IF StoreLock THEN w ELSE "none"
Release(w) == IF holder = w THEN "none" ELSE holder

\* acquire's check: the seal and the tree's digests, or, without SealChecked,
\* only that a tree exists.
Usable == IF SealChecked THEN sealed /\ tree = "complete" ELSE tree # "none"

\* collect keeps a link whose target is an existing directory. Without
\* LiveWhileDir it would keep only links to attempts that have a record.
Live(a) == a \in (IF LiveWhileDir THEN dirs ELSE recorded)

\* The other repository's acquire takes store.lock and, finding the entry
\* unusable, unlinks the seal and starts uv python install.
OtherAcquire ==
    /\ fpc = "idle"
    /\ Free
    /\ ~Usable
    /\ fpc' = "install"
    /\ tree' = "partial"
    /\ sealed' = FALSE
    /\ holder' = Take("other")
    /\ UNCHANGED <<refs, gpc, doomed>>

\* Its install completes and the seal is written last.
OtherInstalled ==
    /\ fpc = "install"
    /\ tree' = "complete"
    /\ sealed' = TRUE
    /\ holder' = Release("other")
    /\ fpc' = "idle"
    /\ UNCHANGED <<refs, gpc, doomed>>

\* An exception or SIGKILL ends its install, leaving the tree unsealed.
OtherStop ==
    /\ fpc = "install"
    /\ fpc' = "idle"
    /\ holder' = Release("other")
    /\ UNCHANGED <<tree, sealed, refs, gpc, doomed>>

\* interpreter_store.collect takes store.lock with LOCK_EX | LOCK_NB and
\* skips while it is held, which changes nothing. It unlinks each link whose
\* attempt directory is gone, and with no link left it unlinks the seal and
\* removes the tree. Under the lock no acquire runs, and an attempt directory
\* deleted while collect runs only keeps the entry longer, so one step stands
\* for the whole lock hold. Without StoreLock, the removal is a later step.
Collect ==
    /\ gpc = "off"
    /\ Free
    /\ LET live == {r \in refs : Live(r)} IN
         /\ refs' = live
         /\ IF ~StoreLock
            THEN /\ gpc' = "trash"
                 /\ doomed' = (live = {})
                 /\ UNCHANGED <<tree, sealed>>
            ELSE /\ UNCHANGED <<gpc, doomed>>
                 /\ IF live = {}
                    THEN /\ tree' = "none"
                         /\ sealed' = FALSE
                    ELSE UNCHANGED <<tree, sealed>>
    /\ UNCHANGED <<holder, fpc>>

\* Without StoreLock: the removal that collect decided on in its scan.
CollectTrash ==
    /\ gpc = "trash"
    /\ IF doomed
       THEN /\ tree' = "none"
            /\ sealed' = FALSE
       ELSE UNCHANGED <<tree, sealed>>
    /\ gpc' = "off"
    /\ doomed' = FALSE
    /\ UNCHANGED <<refs, holder, fpc>>

Next ==
    \/ OtherAcquire \/ OtherInstalled \/ OtherStop \/ Collect \/ CollectTrash
===============================================================================
