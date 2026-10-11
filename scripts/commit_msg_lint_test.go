package scripts_test

import (
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
)

// lintMsg runs scripts/commit-msg-lint.sh on a commit message and returns its output.
func lintMsg(t *testing.T, msg string) (string, error) {
	t.Helper()
	path := filepath.Join(t.TempDir(), "msg")
	if err := os.WriteFile(path, []byte(msg), 0o600); err != nil {
		t.Fatal(err)
	}
	out, err := exec.Command("bash", "commit-msg-lint.sh", path).CombinedOutput()
	return string(out), err
}

// The lint reads only the subject line, so a long squash message cannot fail it (pyvvo/funcd#248): a pipe into
// `head -n1` made the writer die of SIGPIPE under pipefail, and the merge queue refused the PR.
func TestCommitMsgLintIgnoresBodyLength(t *testing.T) {
	t.Parallel()
	body := strings.Repeat("| a table row that a PR body carries into the squash message |\n", 20000)
	lint := func(subject string) (string, error) { return lintMsg(t, "# a comment\n\n"+subject+"\n\n"+body) }
	for range 5 {
		if out, err := lint("fix(ci): a valid subject"); err != nil {
			t.Fatalf("a valid subject passes whatever the body: %v: %s", err, out)
		}
	}
	out, err := lint("not a conventional subject")
	if err == nil {
		t.Fatal("an invalid subject fails")
	}
	if !strings.Contains(out, "\"not a conventional subject\"\n") {
		t.Fatalf("the error quotes the subject line alone: %s", out)
	}
}

// The PR-title action accepts a scope list such as "shim,examples", so the queue's lint accepts it too; it stays
// stricter on empty or malformed scopes.
func TestCommitMsgLintAcceptsAScopeList(t *testing.T) {
	t.Parallel()
	lint := func(subject string) (string, error) { return lintMsg(t, subject+"\n") }
	for _, ok := range []string{
		"fix(shim,examples): a scope list",
		"fix(shim, examples): a scope list with a space",
		"feat(shim)!: a breaking change",
	} {
		if out, err := lint(ok); err != nil {
			t.Fatalf("%q: %v: %s", ok, err, out)
		}
	}
	for _, bad := range []string{"fix(): an empty scope", "fix(,): no scope names", "fix(a,): a trailing comma"} {
		if _, err := lint(bad); err == nil {
			t.Fatalf("%q passes", bad)
		}
	}
}
