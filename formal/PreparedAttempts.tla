--------------------------- MODULE PreparedAttempts ---------------------------
(***************************************************************************)
(* gateseal's prepared attempts in one worktree, the commits that run from *)
(* them, and the activations that publish and delete them. Each worktree   *)
(* has its own preparation directory. Worktrees and repositories share the *)
(* hook and the interpreter store entry that every attempt runs, which     *)
(* InterpreterStore.tla models.                                            *)
(*                                                                         *)
(* Every action names the code it models. One action is one step that the  *)
(* code takes without another actor running in between, or a step where    *)
(* the code reads or writes the worktree's gateseal directory once.        *)
(* The invariants are the numbered prepared-attempt invariants in          *)
(* docs/local-preparation.md.                                              *)
(*                                                                         *)
(* The ten boolean constants are the design decisions the invariants       *)
(* depend on. formal/check.py names the values that match the code and     *)
(* the variants that must break an invariant.                              *)
(***************************************************************************)
EXTENDS Naturals, Sequences, FiniteSets

CONSTANTS
    Commits,        \* commits in this worktree, whose hooks can run at once
    AttemptOrder,   \* the attempt directories that activations create, in order
    MaxActivations, \* how many times gateseal activate runs
    None,
    CommitLock,     \* commits hold attempts.lock shared; cleanup needs it exclusive
    KeepReplaced,   \* cleanup keeps the attempt that current replaced
    DropRestored,   \* cleanup drops a patch whose result the worktree matches
    WaitForOrphans, \* ...and keeps its attempt while its checkout may still run
    HookFirst,      \* _publish installs the hook before it renames current
    RecordLast,     \* _prepare writes prepared.json before _publish runs
    StoreLock,      \* acquire and collect hold store.lock exclusively
    RefBeforeUse,   \* acquire links the attempt into the entry's refs first
    LiveWhileDir,   \* collect keeps a link while its attempt directory exists
    SealChecked     \* acquire uses an entry only when it matches its seal

Attempts == {AttemptOrder[i] : i \in 1..Len(AttemptOrder)}

VARIABLES
    current,     \* target of <git dir>/gateseal/current, or None
    dirs,        \* attempt directories that exist
    recorded,    \* attempts whose prepared.json exists
    replaces,    \* each record's replaces field
    hook,        \* <common dir>/hooks/pre-commit is the gateseal hook
    publishes,   \* renames of current so far
    unhooked,    \* a rename published an attempt while the hook was missing
    activations, \* activations started
    fresh,       \* index in AttemptOrder of the next new attempt
    apc,         \* activation step
    step,        \* position in PublishSteps while apc = "publish"
    new,         \* attempt the running activation builds
    cpc,         \* cleanup step
    todo,        \* stale attempts cleanup has not deleted yet
    at,          \* attempt cleanup is deleting
    scanned,     \* commits whose patch _keep_patches listed in at
    exclusive,   \* cleanup holds attempts.lock exclusively
    holders,     \* commits holding attempts.lock shared
    pc,          \* commit step
    pinned,      \* attempt each commit resolved from current
    seen,        \* publishes when each commit resolved current
    edits,       \* where each commit's unstaged edits are: "worktree" in files
                 \* written before any patch of them, "restored" written back
                 \* after the patch, so the worktree matches it exactly
    patch,       \* where each commit's pre-commit patch file is
    falseReport, \* activation reported a patch whose result the worktree matches
    tree,        \* the store entry's tree: "none", "partial" or "complete"
    sealed,      \* the entry's seal, <sha>.json, exists
    refs,        \* this worktree's attempts linked in <sha>.refs
    holder,      \* store.lock holder while uv installs: "none", "local", "other"
    fpc,         \* "install" while another repository's acquire installs
    gpc,         \* "trash" while a collect without StoreLock removes later
    doomed       \* that collect found no live link

vars == <<current, dirs, recorded, replaces, hook, publishes, unhooked,
          activations, fresh, apc, step, new, cpc, todo, at, scanned,
          exclusive, holders, pc, pinned, seen, edits, patch, falseReport,
          tree, sealed, refs, holder, fpc, gpc, doomed>>

StoreState == <<tree, sealed, refs, holder, fpc, gpc, doomed>>

\* The store's helpers and the other repositories' steps.
Store == INSTANCE InterpreterStore

\* The order of _prepare's record write and _publish's two writes.
PublishSteps ==
    IF RecordLast
    THEN IF HookFirst THEN <<"record", "hook", "rename">>
                      ELSE <<"record", "rename", "hook">>
    ELSE <<"hook", "rename", "record">>

Finished == {"done", "killed", "stopped", "startfailed", "orphan"}
Running == {"start", "lock", "gate", "stash", "checkout", "hooks", "restore", "end"}
AfterGate == {"stash", "checkout", "hooks", "restore", "end"}

TypeOK ==
    /\ current \in Attempts \cup {None}
    /\ dirs \subseteq Attempts
    /\ recorded \subseteq dirs
    /\ replaces \in [Attempts -> Attempts \cup {None}]
    /\ hook \in BOOLEAN
    /\ apc \in {"idle", "clean1", "decide", "acquire", "install", "build",
                "publish", "clean2"}
    /\ cpc \in {"off", "lock", "select", "next", "scan", "move", "delete"}
    /\ holders \subseteq Commits
    /\ pc \in [Commits -> {"idle"} \cup Running \cup Finished]
    /\ edits \in [Commits -> {"none", "worktree", "stashed", "restored", "discarded"}]
    /\ patch \in [Commits -> Attempts \cup {"none", "saved", "dropped", "lost"}]
    /\ tree \in {"none", "partial", "complete"}
    /\ sealed \in BOOLEAN
    /\ refs \subseteq Attempts
    /\ holder \in {"none", "local", "other"}
    /\ fpc \in {"idle", "install"}
    /\ gpc \in {"off", "trash"}
    /\ doomed \in BOOLEAN

Init ==
    /\ current = None
    /\ dirs = {}
    /\ recorded = {}
    /\ replaces = [a \in Attempts |-> None]
    /\ hook = FALSE
    /\ publishes = 0
    /\ unhooked = FALSE
    /\ activations = 0
    /\ fresh = 1
    /\ apc = "idle"
    /\ step = 1
    /\ new = None
    /\ cpc = "off"
    /\ todo = {}
    /\ at = None
    /\ scanned = {}
    /\ exclusive = FALSE
    /\ holders = {}
    /\ pc = [c \in Commits |-> "idle"]
    /\ pinned = [c \in Commits |-> None]
    /\ seen = [c \in Commits |-> 0]
    /\ edits \in [Commits -> {"none", "worktree"}]
    /\ patch = [c \in Commits |-> "none"]
    /\ falseReport = FALSE
    /\ tree = "none"
    /\ sealed = FALSE
    /\ refs = {}
    /\ holder = "none"
    /\ fpc = "idle"
    /\ gpc = "off"
    /\ doomed = FALSE

ActivationVars == <<current, dirs, recorded, replaces, hook, publishes, unhooked,
                    activations, fresh, apc, step, new, cpc, todo, at, scanned,
                    exclusive>>
CommitVars == <<holders, pc, pinned, seen, edits>>
WorktreeVars == <<ActivationVars, CommitVars, patch, falseReport>>

-----------------------------------------------------------------------------
(* Commits. bootstrap.HOOK, bootstrap.main, cli._staged and                 *)
(* runtime.exec_pre_commit, then pre-commit 4.7.0's staged_files_only.      *)

\* bootstrap.HOOK: `cd -P "${GIT_DIR:-.git}/gateseal/current" && pwd -P` pins
\* one physical attempt of the worktree Git commits in. git commit releases
\* index.lock before it runs the hook of an ordinary commit, so the hooks of
\* several commits can run at once.
Resolve(c) ==
    /\ pc[c] = "idle"
    /\ IF current = None
       THEN /\ pc' = [pc EXCEPT ![c] = "stopped"]
            /\ UNCHANGED <<pinned, seen>>
       ELSE /\ pc' = [pc EXCEPT ![c] = "start"]
            /\ pinned' = [pinned EXCEPT ![c] = current]
            /\ seen' = [seen EXCEPT ![c] = publishes]
    /\ UNCHANGED <<ActivationVars, holders, edits, patch, falseReport>>
    /\ UNCHANGED StoreState

\* bootstrap.HOOK checks the attempt's hook/python link, then execs the
\* store entry's interpreter, which loads its standard library from the entry.
Start(c) ==
    /\ pc[c] = "start"
    /\ pc' = [pc EXCEPT ![c] = IF pinned[c] \notin dirs \/ tree = "none"
                               THEN "startfailed"
                               ELSE IF CommitLock THEN "lock" ELSE "gate"]
    /\ UNCHANGED <<ActivationVars, holders, pinned, seen, edits, patch, falseReport>>
    /\ UNCHANGED StoreState

\* bootstrap.main: flock(attempts.lock, LOCK_SH) blocks while cleanup holds
\* LOCK_EX. The descriptor is inheritable, so pre-commit holds it until exit.
Lock(c) ==
    /\ pc[c] = "lock"
    /\ ~exclusive
    /\ holders' = holders \cup {c}
    /\ pc' = [pc EXCEPT ![c] = "gate"]
    /\ UNCHANGED <<ActivationVars, pinned, seen, edits, patch, falseReport>>
    /\ UNCHANGED StoreState

\* bootstrap.main reads the pinned record and checks the staged files;
\* cli._staged reads it again and execs pre-commit from the attempt.
Gate(c) ==
    /\ pc[c] = "gate"
    /\ IF pinned[c] \notin recorded
       THEN /\ pc' = [pc EXCEPT ![c] = "stopped"]
            /\ holders' = holders \ {c}
       ELSE /\ pc' = [pc EXCEPT ![c] = IF edits[c] = "worktree" THEN "stash"
                                                                ELSE "hooks"]
            /\ UNCHANGED holders
    /\ UNCHANGED <<ActivationVars, pinned, seen, edits, patch, falseReport>>
    /\ UNCHANGED StoreState

\* staged_files_only: os.makedirs(patch_dir, exist_ok=True), then the patch
\* write. makedirs recreates the attempt path, without its record, if cleanup
\* deleted it.
WritePatch(c) ==
    /\ pc[c] = "stash"
    /\ patch' = [patch EXCEPT ![c] = pinned[c]]
    /\ dirs' = dirs \cup {pinned[c]}
    /\ pc' = [pc EXCEPT ![c] = "checkout"]
    /\ UNCHANGED <<current, recorded, replaces, hook, publishes, unhooked,
                   activations, fresh, apc, step, new, cpc, todo, at, scanned,
                   exclusive, holders, pinned, seen, edits, falseReport>>
    /\ UNCHANGED StoreState

\* staged_files_only: `git checkout -- .` clears the unstaged edits.
Checkout(c) ==
    /\ pc[c] = "checkout"
    /\ edits' = [edits EXCEPT ![c] = "stashed"]
    /\ pc' = [pc EXCEPT ![c] = "hooks"]
    /\ UNCHANGED <<ActivationVars, holders, pinned, seen, patch, falseReport>>
    /\ UNCHANGED StoreState

\* pre-commit runs the hooks from the attempt's pre-commit home.
Hooks(c) ==
    /\ pc[c] = "hooks"
    /\ pc' = [pc EXCEPT ![c] = IF edits[c] = "stashed" THEN "restore" ELSE "end"]
    /\ UNCHANGED <<ActivationVars, holders, pinned, seen, edits, patch, falseReport>>
    /\ UNCHANGED StoreState

\* staged_files_only's finally: `git apply` of the patch path. It works only
\* if the file is still there, and pre-commit never deletes it afterwards.
Restore(c) ==
    /\ pc[c] = "restore"
    /\ edits' = [edits EXCEPT ![c] = IF patch[c] = pinned[c] /\ pinned[c] \in dirs
                                     THEN "restored" ELSE @]
    /\ pc' = [pc EXCEPT ![c] = "end"]
    /\ UNCHANGED <<ActivationVars, holders, pinned, seen, patch, falseReport>>
    /\ UNCHANGED StoreState

\* pre-commit exits and its attempts.lock descriptor closes.
End(c) ==
    /\ pc[c] = "end"
    /\ holders' = holders \ {c}
    /\ pc' = [pc EXCEPT ![c] = "done"]
    /\ UNCHANGED <<ActivationVars, pinned, seen, edits, patch, falseReport>>
    /\ UNCHANGED StoreState

\* SIGKILL or power loss: no finally runs, and the lock closes with the process.
\* pre-commit runs `git checkout -- .` as a child, and subprocess closes the
\* inherited lock descriptor, so a checkout already started can outlive it.
Kill(c) ==
    /\ pc[c] \in Running
    /\ holders' = holders \ {c}
    /\ pc' = [pc EXCEPT ![c] = IF pc[c] = "checkout" THEN "orphan" ELSE "killed"]
    /\ UNCHANGED <<ActivationVars, pinned, seen, edits, patch, falseReport>>
    /\ UNCHANGED StoreState

\* The orphaned `git checkout -- .` clears the edits without the lock. An
\* orphan that never takes this step died with pre-commit.
OrphanCheckout(c) ==
    /\ pc[c] = "orphan"
    /\ edits' = [edits EXCEPT ![c] = "stashed"]
    /\ pc' = [pc EXCEPT ![c] = "killed"]
    /\ UNCHANGED <<ActivationVars, holders, pinned, seen, patch, falseReport>>
    /\ UNCHANGED StoreState

\* The user later changes or discards a file that holds the edits, so the
\* worktree no longer matches the patch's result.
Discard(c) ==
    /\ pc[c] \in {"done", "killed"}
    /\ edits[c] \in {"worktree", "restored"}
    /\ edits' = [edits EXCEPT ![c] = "discarded"]
    /\ UNCHANGED <<ActivationVars, holders, pc, pinned, seen, patch, falseReport>>
    /\ UNCHANGED StoreState

-----------------------------------------------------------------------------
(* Activations. cli._operation and upgrade._activate hold operation.lock, *)
(* so one runs at a time.                                                  *)
(* runtime.activate: cleanup, then a healthy return or _prepare and         *)
(* _publish, then cleanup again in its finally.                             *)

Begin ==
    /\ apc = "idle"
    /\ activations < MaxActivations
    /\ activations' = activations + 1
    /\ apc' = "clean1"
    /\ cpc' = "lock"
    /\ UNCHANGED <<current, dirs, recorded, replaces, hook, publishes, unhooked,
                   fresh, step, new, todo, at, scanned, exclusive,
                   holders, pc, pinned, seen, edits, patch, falseReport>>
    /\ UNCHANGED StoreState

\* runtime.activate after the first cleanup: _healthy returns early, or
\* _prepare creates a new attempt directory and builds it.
Decide ==
    /\ apc = "clean1"
    /\ cpc = "off"
    /\ apc' = "decide"
    /\ UNCHANGED <<current, dirs, recorded, replaces, hook, publishes, unhooked,
                   activations, fresh, step, new, cpc, todo, at, scanned,
                   exclusive, holders, pc, pinned, seen, edits, patch, falseReport>>
    /\ UNCHANGED StoreState

\* _healthy compares every tree of the current record, the store entry among
\* them, with its content digests.
Healthy ==
    /\ apc = "decide"
    /\ current # None
    /\ hook
    /\ tree = "complete"
    /\ apc' = "clean2"
    /\ cpc' = "lock"
    /\ UNCHANGED <<current, dirs, recorded, replaces, hook, publishes, unhooked,
                   activations, fresh, step, new, todo, at, scanned, exclusive,
                   holders, pc, pinned, seen, edits, patch, falseReport>>
    /\ UNCHANGED StoreState

Prepare ==
    /\ apc = "decide"
    /\ fresh <= Len(AttemptOrder)
    /\ new' = AttemptOrder[fresh]
    /\ fresh' = fresh + 1
    /\ dirs' = dirs \cup {AttemptOrder[fresh]}
    /\ apc' = "acquire"
    /\ UNCHANGED <<current, recorded, replaces, hook, publishes, unhooked,
                   activations, step, cpc, todo, at, scanned, exclusive,
                   holders, pc, pinned, seen, edits, patch, falseReport>>
    /\ UNCHANGED StoreState

\* interpreter_store.sealed for new, which acquire calls for an interpreter
\* and hook_store.prepare for a hook entry: flock(store.lock, LOCK_EX) waits,
\* then the link <sha>.refs/<new>, then the seal and both digests. A usable
\* entry is used; otherwise the seal is unlinked and the build starts.
Acquire ==
    /\ apc = "acquire"
    /\ Store!Free
    /\ refs' = IF RefBeforeUse THEN refs \cup {new} ELSE refs
    /\ IF Store!Usable
       THEN /\ apc' = "build"
            /\ UNCHANGED <<tree, sealed, holder>>
       ELSE /\ apc' = "install"
            /\ tree' = "partial"
            /\ sealed' = FALSE
            /\ holder' = Store!Take("local")
    /\ UNCHANGED <<current, dirs, recorded, replaces, hook, publishes, unhooked,
                   activations, fresh, step, new, cpc, todo, at, scanned,
                   exclusive, holders, pc, pinned, seen, edits, patch,
                   falseReport, fpc, gpc, doomed>>

\* sealed: uv python install or pre-commit install-hooks, read-only
\* normalization, then the seal last.
Installed ==
    /\ apc = "install"
    /\ tree' = "complete"
    /\ sealed' = TRUE
    /\ holder' = Store!Release("local")
    /\ apc' = "build"
    /\ UNCHANGED <<current, dirs, recorded, replaces, hook, publishes, unhooked,
                   activations, fresh, step, new, cpc, todo, at, scanned,
                   exclusive, holders, pc, pinned, seen, edits, patch,
                   falseReport, refs, fpc, gpc, doomed>>

\* _prepare finishes uv, pre-commit and hook preparation. uv venv and the hook
\* environments need the entry's interpreter. A design that links new only now
\* does so without the store lock.
Built ==
    /\ apc = "build"
    /\ tree # "none"
    /\ refs' = IF RefBeforeUse THEN refs ELSE refs \cup {new}
    /\ apc' = "publish"
    /\ step' = 1
    /\ UNCHANGED <<current, dirs, recorded, replaces, hook, publishes, unhooked,
                   activations, fresh, new, cpc, todo, at, scanned, exclusive,
                   holders, pc, pinned, seen, edits, patch, falseReport>>
    /\ UNCHANGED <<tree, sealed, holder, fpc, gpc, doomed>>

\* _prepare: write_atomic(prepared.json) with replaces = _current_name.
\* _publish: write_atomic(hook) if it is missing, then os.replace(current).
PublishStep ==
    /\ apc = "publish"
    /\ LET s == PublishSteps[step] IN
         /\ recorded' = IF s = "record" THEN recorded \cup {new} ELSE recorded
         /\ replaces' = IF s = "record" THEN [replaces EXCEPT ![new] = current]
                                        ELSE replaces
         /\ hook' = IF s = "hook" THEN TRUE ELSE hook
         /\ current' = IF s = "rename" THEN new ELSE current
         /\ publishes' = IF s = "rename" THEN publishes + 1 ELSE publishes
         /\ unhooked' = (unhooked \/ (s = "rename" /\ ~hook))
    /\ IF step = Len(PublishSteps)
       THEN /\ apc' = "clean2"
            /\ cpc' = "lock"
            /\ step' = 1
       ELSE /\ step' = step + 1
            /\ UNCHANGED <<apc, cpc>>
    /\ UNCHANGED <<dirs, activations, fresh, new, todo, at, scanned, exclusive,
                   holders, pc, pinned, seen, edits, patch, falseReport>>
    /\ UNCHANGED StoreState

\* An exception in _prepare or _publish: activate's finally runs cleanup.
\* An exception inside acquire leaves the entry unsealed and closes the lock.
Fail ==
    /\ apc \in {"acquire", "install", "build", "publish"}
    /\ apc' = "clean2"
    /\ cpc' = "lock"
    /\ step' = 1
    /\ holder' = Store!Release("local")
    /\ UNCHANGED <<current, dirs, recorded, replaces, hook, publishes, unhooked,
                   activations, fresh, new, todo, at, scanned, exclusive,
                   holders, pc, pinned, seen, edits, patch, falseReport>>
    /\ UNCHANGED <<tree, sealed, refs, fpc, gpc, doomed>>

\* activate returns once its final cleanup ends.
Finish ==
    /\ apc = "clean2"
    /\ cpc = "off"
    /\ apc' = "idle"
    /\ new' = None
    /\ UNCHANGED <<current, dirs, recorded, replaces, hook, publishes, unhooked,
                   activations, fresh, step, cpc, todo, at, scanned, exclusive,
                   holders, pc, pinned, seen, edits, patch, falseReport>>
    /\ UNCHANGED StoreState

\* SIGKILL or power loss at any point: no finally runs, and the flocks close.
\* uv inherits store.lock, so a killed install keeps it until uv exits, and
\* leaves an unsealed tree.
Crash ==
    /\ apc # "idle"
    /\ apc' = "idle"
    /\ cpc' = "off"
    /\ step' = 1
    /\ new' = None
    /\ todo' = {}
    /\ at' = None
    /\ scanned' = {}
    /\ exclusive' = FALSE
    /\ holder' = Store!Release("local")
    /\ UNCHANGED <<current, dirs, recorded, replaces, hook, publishes, unhooked,
                   activations, fresh, holders, pc, pinned, seen, edits, patch,
                   falseReport>>
    /\ UNCHANGED <<tree, sealed, refs, fpc, gpc, doomed>>

-----------------------------------------------------------------------------
(* runtime._clean_up and runtime._keep_patches.                             *)

\* flock(attempts.lock, LOCK_EX | LOCK_NB): a held shared lock skips cleanup.
CleanLock ==
    /\ cpc = "lock"
    /\ IF ~CommitLock
       THEN /\ cpc' = "select"
            /\ UNCHANGED exclusive
       ELSE IF holders = {}
            THEN /\ cpc' = "select"
                 /\ exclusive' = TRUE
            ELSE /\ cpc' = "off"
                 /\ UNCHANGED exclusive
    /\ UNCHANGED <<current, dirs, recorded, replaces, hook, publishes, unhooked,
                   activations, fresh, apc, step, new, todo, at, scanned,
                   holders, pc, pinned, seen, edits, patch, falseReport>>
    /\ UNCHANGED StoreState

\* keep = {current} plus current's replaces; every other attempt is stale.
CleanSelect ==
    /\ cpc = "select"
    /\ LET keep == {current} \cup
                   (IF KeepReplaced /\ current \in recorded
                    THEN {replaces[current]} ELSE {})
       IN todo' = dirs \ keep
    /\ cpc' = "next"
    /\ UNCHANGED <<current, dirs, recorded, replaces, hook, publishes, unhooked,
                   activations, fresh, apc, step, new, at, scanned, exclusive,
                   holders, pc, pinned, seen, edits, patch, falseReport>>
    /\ UNCHANGED StoreState

\* os.scandir yields the stale attempts in any order.
CleanNext ==
    /\ cpc = "next"
    /\ IF todo = {}
       THEN /\ cpc' = "off"
            /\ exclusive' = FALSE
            /\ UNCHANGED <<todo, at>>
       ELSE /\ \E a \in todo : /\ at' = a
                               /\ todo' = todo \ {a}
            /\ cpc' = "scan"
            /\ UNCHANGED exclusive
    /\ UNCHANGED <<current, dirs, recorded, replaces, hook, publishes, unhooked,
                   activations, fresh, apc, step, new, scanned, holders, pc,
                   pinned, seen, edits, patch, falseReport>>
    /\ UNCHANGED StoreState

\* _keep_patches lists the patch files in at's pre-commit home.
CleanScan ==
    /\ cpc = "scan"
    /\ scanned' = {c \in Commits : patch[c] = at}
    /\ cpc' = "move"
    /\ UNCHANGED <<current, dirs, recorded, replaces, hook, publishes, unhooked,
                   activations, fresh, apc, step, new, todo, at, exclusive,
                   holders, pc, pinned, seen, edits, patch, falseReport>>
    /\ UNCHANGED StoreState

\* _keep_patches drops each listed patch whose result the worktree matches
\* exactly, as _restored checks it, or moves it to <git dir>/gateseal/patches.
\* _patch_steps reports every patch in that folder with a git apply step.
\* A matching patch younger than PATCH_GRACE_SECONDS waits instead, and its
\* attempt stays. The model has no clock, so a patch waits exactly while its
\* orphaned checkout may still run, which assumes the grace period outlasts
\* every orphan. The code waits longer, which only keeps attempts longer.
CleanMove ==
    /\ cpc = "move"
    /\ LET matching == {c \in scanned : DropRestored /\ edits[c] \in {"worktree", "restored"}}
           waiting == {c \in matching : WaitForOrphans /\ pc[c] = "orphan"}
           dropped == matching \ waiting
           saved == scanned \ matching
       IN /\ patch' = [c \in Commits |-> IF c \in dropped THEN "dropped"
                                         ELSE IF c \in saved THEN "saved"
                                         ELSE patch[c]]
          /\ falseReport' = (falseReport \/ \E c \in saved : edits[c] = "restored")
          /\ cpc' = IF waiting = {} THEN "delete" ELSE "next"
    /\ UNCHANGED <<current, dirs, recorded, replaces, hook, publishes, unhooked,
                   activations, fresh, apc, step, new, todo, at, scanned,
                   exclusive, holders, pc, pinned, seen, edits>>
    /\ UNCHANGED StoreState

\* shutil.rmtree deletes at with everything still inside it.
CleanDelete ==
    /\ cpc = "delete"
    /\ dirs' = dirs \ {at}
    /\ recorded' = recorded \ {at}
    /\ patch' = [c \in Commits |-> IF patch[c] = at THEN "lost" ELSE patch[c]]
    /\ cpc' = "next"
    /\ UNCHANGED <<current, replaces, hook, publishes, unhooked, activations,
                   fresh, apc, step, new, todo, at, scanned, exclusive,
                   holders, pc, pinned, seen, edits, falseReport>>
    /\ UNCHANGED StoreState

\* Another worktree's gateseal activate: _publish writes the shared
\* hooks/pre-commit, with the same bootstrap.HOOK bytes, when it is missing.
ForeignHook ==
    /\ ~hook
    /\ hook' = TRUE
    /\ UNCHANGED <<current, dirs, recorded, replaces, publishes, unhooked,
                   activations, fresh, apc, step, new, cpc, todo, at, scanned,
                   exclusive, holders, pc, pinned, seen, edits, patch, falseReport>>
    /\ UNCHANGED StoreState

-----------------------------------------------------------------------------

Done ==
    /\ \A c \in Commits : pc[c] \in Finished
    /\ apc = "idle"
    /\ activations = MaxActivations
    /\ UNCHANGED vars

Commit(c) ==
    \/ Resolve(c) \/ Start(c) \/ Lock(c) \/ Gate(c) \/ WritePatch(c)
    \/ Checkout(c) \/ Hooks(c) \/ Restore(c) \/ End(c) \/ Kill(c)
    \/ OrphanCheckout(c) \/ Discard(c)

Activation ==
    \/ Begin \/ Decide \/ Healthy \/ Prepare \/ Acquire \/ Installed
    \/ Built \/ PublishStep \/ Fail \/ Finish \/ Crash \/ ForeignHook
    \/ CleanLock \/ CleanSelect \/ CleanNext \/ CleanScan \/ CleanMove
    \/ CleanDelete

Next ==
    \/ \E c \in Commits : Commit(c)
    \/ Activation
    \/ Store!Next /\ UNCHANGED WorktreeVars
    \/ Done

Spec == Init /\ [][Next]_vars

-----------------------------------------------------------------------------
(* The prepared-attempt invariants in local-preparation.md.                 *)

\* 1. current is absent or names an attempt whose prepared.json exists.
CurrentIsComplete == current = None \/ current \in recorded

\* 2. A commit past its gate runs from the attempt it pinned, and cleanup does
\*    not delete that attempt until the commit ends.
PinnedAttemptLives ==
    \A c \in Commits : pc[c] \in AfterGate => pinned[c] \in recorded

\* 3. Unstaged edits that pre-commit cleared are in a patch file that still
\*    exists, in its attempt or in <git dir>/gateseal/patches.
NoLostEdits ==
    \A c \in Commits : edits[c] = "stashed" => (patch[c] \in dirs \/ patch[c] = "saved")

\* 3. Activation reports no patch that pre-commit restored and the user left
\*    unchanged.
TruthfulReports == ~falseReport

\* 4. Activation never publishes an attempt while the hook is missing.
HookBeforePublish == ~unhooked

\* 9. Every attempt with a record is linked in the entry's refs, and the
\*    entry stays complete and sealed. With PinnedAttemptLives, a commit past
\*    its gate runs a complete interpreter.
InterpreterLives ==
    \A a \in recorded : a \in refs /\ sealed /\ tree = "complete"

\* A commit stops before its gate because cleanup deleted its attempt only
\* after two publishes since it resolved current.
StartupFailureNeedsTwoPublishes ==
    \A c \in Commits : pc[c] = "startfailed" => publishes - seen[c] >= 2

=============================================================================
