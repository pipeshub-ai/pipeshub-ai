#!/usr/bin/perl
# Run by pg-restore.sh in a one-off pg-backup container (program on stdin) before anything is restored:
#   perl - time|lsn <target> [<backup label>]
# Exits 4, naming the latest reachable point and the backup to restore it from, when the WAL in the pgBackRest repository cannot carry
# recovery from the backup pgBackRest would pick to the target. Recovery "will shut down with a fatal error" when "archive recovery
# ends before the target is reached" (https://www.postgresql.org/docs/17/runtime-config-wal.html, recovery_target_action), and with
# restart: always the server would crash-loop. Exits 0 when the target is reachable, and also when it cannot tell (with a warning), so
# pgBackRest and PostgreSQL report what this does not cover.
#
# Everything is read from the repository itself, with the commands that work on every repository type and decrypt
# (https://pgbackrest.org/command.html): `info` for the backups and their WAL range ("archive start/stop"), `repo-ls --recurse` for the
# archived segments and timeline history files, `repo-get` for the content of one (compressed as archived, so it is decompressed here).
# archive-get would decompress too, but it reads pg_control from the data directory, which an empty volume after a lost machine lacks.
use strict;
use warnings;
use JSON::PP;
use Time::Local qw(timegm timelocal);
use File::Temp qw(tempdir);
use POSIX qw(strftime floor);

my ($type, $target, $set) = @ARGV;
my $stanza = $ENV{PGBACKREST_STANZA} or die "pg-restore: PGBACKREST_STANZA is not set\n";
my $tmp = tempdir(CLEANUP => 1);
sub cantell { print STDERR "pg-restore: warning: $_[0]; restoring without checking that the target is reachable\n"; exit 0; }

sub out {   # stdout of a command (no shell; stderr discarded), or undef when it fails; a leading 1 keeps the output of a failed one
  my $any = $_[0] eq '1' ? shift : 0;
  my $pid = open(my $fh, '-|') // return undef;
  if (!$pid) { open(STDERR, '>', '/dev/null'); exec { $_[0] } @_; exit 127; }
  binmode $fh; local $/; my $o = <$fh>;
  close $fh or $any or return undef;
  return $o // '';
}

# pgBackRest's own time format for --target (src/command/restore/select.c.inc: "YYYY-MM-DD HH:MM:SS with optional msec and optional
# timezone (+/- HH or HHMM or HH:MM) - if timezone is omitted, local time is assumed")
sub epoch {
  my ($s) = @_;
  $s =~ /^(\d{4})-(\d\d)-(\d\d)[ T](\d\d):(\d\d):(\d\d)(\.\d+)?\s*(?:(Z|UTC|GMT)|([+-])(\d\d)(?::?(\d\d))?)?$/ or return undef;
  my @t = ($6, $5, $4, $3, $2 - 1, $1);
  my $frac = $7 // 0;
  return timelocal(@t) + $frac unless defined $8 || defined $9;
  my $off = defined $9 ? ($9 eq '-' ? -1 : 1) * ($10 * 3600 + ($11 // 0) * 60) : 0;
  return timegm(@t) - $off + $frac;
}
sub utc { my ($e) = @_; my $s = floor($e); return strftime('%Y-%m-%d %H:%M:%S', gmtime $s) . sprintf('.%06d+00', ($e - $s) * 1e6); }
sub lsn { my ($h, $l) = $_[0] =~ m{^([0-9A-Fa-f]+)/([0-9A-Fa-f]+)$} or return undef; return hex($h) * 2**32 + hex($l); }

my $want;
if ($type eq 'time') { $want = epoch($target) // exit 0; }   # pgBackRest names a format it rejects itself
else { $want = lsn($target) // exit 0; }

my $info = out(qw(pgbackrest --log-level-console=warn --output=json info)) // cantell('pgbackrest info failed');
my ($st) = grep { $_->{name} eq $stanza } @{ decode_json($info) };
$st or cantell("the repository has no stanza $stanza");
my @repos = sort { $a <=> $b } map { $_->{key} } @{ $st->{repo} || [] };
my @backups = sort { $a->{timestamp}{stop} <=> $b->{timestamp}{stop} } @{ $st->{backup} || [] };

# The backup pgBackRest picks (select.c.inc): the given set; else, repository by repository, the newest one whose stop time is before the
# target time, or whose stop LSN is at or before the target LSN. None: pgBackRest says so itself.
my $bk;
if (defined $set && $set ne '') { ($bk) = grep { $_->{label} eq $set } @backups; }
else {
  for my $r (@repos) {
    ($bk) = reverse grep {
      $_->{database}{'repo-key'} == $r
        && ($type eq 'time' ? $_->{timestamp}{stop} < int($want) : defined $_->{lsn}{stop} && lsn($_->{lsn}{stop}) <= $want)
    } @backups;
    last if $bk;
  }
}
exit 0 unless $bk && $bk->{archive}{stop};
my $repo = $bk->{database}{'repo-key'};
my ($arch) = grep { $_->{database}{id} == $bk->{database}{id} && $_->{database}{'repo-key'} == $repo } @{ $st->{archive} || [] };
$arch or cantell("no archive is listed for backup $bk->{label}");
my $dir = "archive/$stanza/$arch->{id}";

# Every archived segment and history file, from every repository: archive-get falls back to the next repository for a missing segment
my (%seg, %hist);   # $seg{segment number}{timeline} = [repo, path]; $hist{timeline} = [repo, path]
for my $r (@repos) {
  my $ls = out('pgbackrest', '--log-level-console=warn', "--repo=$r", '--output=json', '--recurse', 'repo-ls', $dir)
    // cantell("could not list the archive of repository $r, so a segment missing from it cannot be told from one not read");
  for my $p (keys %{ decode_json($ls) }) {
    my $f = (split m{/}, $p)[-1];
    if ($f =~ /^([0-9A-F]{8})([0-9A-F]{8})([0-9A-F]{8})-[0-9a-f]{40}(?:\.\w+)?$/) {   # not .partial or .backup
      $seg{"$2$3"}{hex $1} //= [$r, "$dir/$p"];
    } elsif ($f =~ /^([0-9A-F]{8})\.history(?:\.\w+)?$/) { $hist{hex $1} //= [$r, "$dir/$p"]; }
  }
}

sub fetch {   # [repo, path] -> a local copy, decompressed, named $name
  my ($at, $name) = @_;
  my $raw = out('pgbackrest', '--log-level-console=warn', "--repo=$at->[0]", 'repo-get', $at->[1]) // cantell("could not read $at->[1]");
  my %tool = (gz => 'gzip', zst => 'zstd', lz4 => 'lz4', bz2 => 'bzip2');
  my ($ext) = $at->[1] =~ /\.(gz|zst|lz4|bz2)$/;
  open(my $fh, '>', "$tmp/raw") or die $!; binmode $fh; print $fh $raw; close $fh;
  my $data = $ext ? out($tool{$ext}, '-dc', "$tmp/raw") // cantell("could not decompress $at->[1] with $tool{$ext}") : $raw;
  open($fh, '>', "$tmp/$name") or die $!; binmode $fh; print $fh $data; close $fh;
  return length $data;
}

# segment numbers: <log><seg> over a segment size read from the backup's own last segment
my $stop = $bk->{archive}{stop};
my $btl = hex substr($stop, 0, 8);
my $at = $seg{ substr($stop, 8) }{$btl} or do {
  print STDERR "pg-restore: the repository lacks WAL segment $stop, which backup $bk->{label} needs to be consistent; nothing was restored\n";
  exit 4;
};
my $segsize = fetch($at, $stop) or cantell("WAL segment $stop is empty");
my $per = 2**32 / $segsize;
sub num { my ($k) = @_; return hex(substr($k, 0, 8)) * $per + hex(substr($k, 8, 8)); }
sub key { my ($n) = @_; return sprintf('%08X%08X', floor($n / $per), $n % $per); }

# Recovery follows the newest timeline (recovery_target_timeline 'latest') through its history file: lines of "<parent timeline>
# <switch LSN> <reason>" (https://www.postgresql.org/docs/17/continuous-archiving.html, 25.3.6). A segment counts on every timeline of
# that history whose start lies at or before it, newest first, as recovery reads it.
my ($last) = sort { $b <=> $a } ($btl, keys %hist);
my @chain = ([$last, 0]);   # [timeline, first segment number], newest first
if ($last != $btl && fetch($hist{$last}, 'history')) {
  open(my $fh, '<', "$tmp/history") or die $!;
  my @sw = map { /^\s*(\d+)\s+([0-9A-Fa-f]+\/[0-9A-Fa-f]+)/ ? [$1, floor(lsn($2) / $segsize)] : () } <$fh>;
  $chain[0][1] = $sw[-1][1] if @sw;
  push @chain, [$sw[$_][0], $_ ? $sw[$_ - 1][1] : 0] for reverse 0 .. $#sw;
}
sub file { my ($n) = @_; for my $c (@chain) { next if $c->[1] > $n; my $f = $seg{ key($n) }{ $c->[0] }; return [$f, $c->[0]] if $f; } return; }

my $end = num(substr($stop, 8));
$end++ while file($end + 1);

# pg_waldump of one segment: the LSN of its last record and the time of its last commit or abort (the records a time target stops at)
sub walinfo {
  my ($n) = @_;
  my ($f, $tl) = @{ file($n) || return };
  my $name = sprintf('%08X', $tl) . key($n);
  fetch($f, $name);
  # it ends each segment with an error (the next one is not there): the output counts, not the status
  my $o = out(1, 'pg_waldump', "$tmp/$name") // '';
  unlink "$tmp/$name";
  my ($rec, $t);
  for (split /\n/, $o) {
    $rec = $1 if /\blsn: ([0-9A-F]+\/[0-9A-F]+)/;
    if (/desc: (?:COMMIT|ABORT)(?:_PREPARED)? (?:\d+: )?(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d(?:\.\d+)?) (\S+)/) {
      my ($when, $zone) = ($1, $2);
      $t = epoch($when . ($zone =~ /^(UTC|GMT)$/ ? '+00' : ''));
    }
  }
  return ($rec, $t);
}
sub segname { my ($n) = @_; my $f = file($n); return sprintf('%08X', $f ? $f->[1] : $chain[0][0]) . key($n); }
my ($rec, $commit) = walinfo($end);
defined $rec or cantell('pg_waldump could not read WAL segment ' . segname($end));
# recovery to an LSN stops at the first record at or after it ("record->ReadRecPtr >= recoveryTargetLSN", recoveryStopsAfter in
# src/backend/access/transam/xlogrecovery.c, with recovery_target_inclusive on, the default): no commit is needed
exit 0 if $type eq 'lsn' && lsn($rec) >= $want;
# back to the backup's own last segment, or 64 segments at most: commits after its end in none of them means none in reach at all
my $n = $end;
for (; !defined $commit && $n > num(substr($stop, 8)) && $n > $end - 64; $n--) { (undef, $commit) = walinfo($n - 1); }
my $none = !defined $commit && $n == num(substr($stop, 8));

if ($type eq 'time') {
  cantell('no commit was found in the last 64 WAL segments the backup reaches') unless defined $commit || $none;
  # recovery to a time stops only at a commit or abort later than it: "stopsHere = (recordXtime > recoveryTargetTime)" with
  # recovery_target_inclusive on (recoveryStopsBefore, xlogrecovery.c), so a commit exactly at the target does not stop it
  exit 0 if defined $commit && $commit > $want;
}

print STDERR "pg-restore: the target $target cannot be reached: from backup $bk->{label}, the repository's WAL runs without a break only",
  " to segment ", segname($end), " (record $rec); segment ", segname($end + 1), " is not in the repository (WAL dropped there, or not archived yet).",
  " Recovery would end before the target and the server would stop. Nothing was restored.\n";
print STDERR "pg-restore: the latest reachable point from that backup is ",
  (defined $commit ? "its last commit, at " . utc($commit) : $none ? "the end of the backup itself (no commit after it is archived)"
   : "record $rec (no commit found near it)"),
  ": restore it with: scripts/pg-restore.sh latest --set $bk->{label}\n";
my ($after) = grep { $_->{database}{'repo-key'} == $repo && num(substr($_->{archive}{start}, 8)) > $end } @backups;
print STDERR "pg-restore: backup $after->{label} begins after that point (it stopped at ", utc($after->{timestamp}{stop}),
  "): a target after that time is reached from it\n" if $after;
exit 4;
