---------------------------------- MODULE MC ----------------------------------
EXTENDS PreparedAttempts, TLC

MCAttemptOrder == <<"a1", "a2", "a3">>

MCSymmetry == Permutations(Commits)
=============================================================================
