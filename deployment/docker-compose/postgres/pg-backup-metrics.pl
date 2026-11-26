#!/usr/bin/perl
# Prometheus metrics of the `pg-backup` service (pg-backup.sh), in two modes:
#
#   pg-backup-metrics.pl write <new info JSON file> <state dir> <gap dir> <full interval s> <incr interval s> <metrics file>
#     Reads `pgbackrest --output=json info` as the scheduler just took it (an empty file when that failed). A readable stanza replaces
#     <state dir>/info.json; otherwise the last readable one is kept, so the backup times stay exported (and age) while the repository
#     cannot be read, and repository_up says so. Writes the metrics file atomically.
#   pg-backup-metrics.pl serve <port> <metrics file> <gap dir>
#     Serves that file at any path over plain HTTP, one request at a time. The dropped-WAL count is read from <gap dir> on every request:
#     the file is rewritten between backups, and a drop during a backup that runs for hours must not wait for it.
#
# Field names of the info JSON (pgBackRest 2.60.0, src/command/info/info.c): each stanza has "name", "status" {"code"} and "backup",
# a list of {"type" (full, diff, incr), "timestamp" {"start", "stop"} in epoch seconds, "database" {"repo-key"}}. Status code 0 is
# "ok" and 2 "no valid backups"; any other code is a stanza in error.
use strict;
use warnings;
use JSON::PP;

my $mode = shift // '';
if ($mode eq 'write') { write_metrics(@ARGV) }
elsif ($mode eq 'serve') { serve(@ARGV) }
else { die "usage: $0 write <info> <state dir> <gap dir> <full s> <incr s> <metrics> | serve <port> <metrics> <gap dir>\n" }

sub slurp { my ($f) = @_; open(my $h, '<', $f) or return ''; local $/; my $t = <$h>; close $h; return $t // '' }

# (the stanzas, undef) when every one is readable (status 0 or 2), else (undef, why not). Lines before the first one opening with [ or {
# are skipped: pgBackRest can print a warning on stdout ahead of the JSON (see info_json in pg-backup.sh).
sub stanzas {
    my ($text) = @_;
    my @lines = split /^/m, $text;
    shift @lines while @lines && $lines[0] !~ /^[\[{]/;
    return (undef, 'pgbackrest info printed no JSON (it failed or the repository is unreachable)') unless @lines;
    $text = join '', @lines;
    my $doc = eval { decode_json($text) };
    unless (ref $doc eq 'ARRAY') {
        my $err = $@ ? (split /\n/, $@)[0] : 'not a list of stanzas';
        return (undef, "not JSON: $err");
    }
    for my $s (@$doc) {
        my $c = $s->{status}{code} // 99;
        return (undef, sprintf('stanza %s: status %s (%s)', $s->{name} // '?', $c, $s->{status}{message} // '')) unless $c == 0 || $c == 2;
    }
    return ($doc, undef);
}

sub gap_segments { my ($gaps) = @_; my $n = 0; if (opendir(my $d, $gaps)) { $n = grep { !/^\.\.?$/ } readdir $d; closedir $d } return $n }

sub esc { my ($v) = @_; $v =~ s/(["\\])/\\$1/g; $v =~ s/\n/\\n/g; return $v }

sub write_metrics {
    my ($new, $state, $gaps, $full_s, $incr_s, $out) = @_;
    die "write: six arguments\n" unless defined $out;
    my $up = 0;
    my ($doc, $why) = stanzas(slurp($new));
    if ($doc) {
        $up = 1;
        open(my $h, '>', "$state/info.json.tmp") or die "cannot write $state/info.json.tmp: $!\n";
        print $h slurp($new); close $h or die "cannot write $state/info.json.tmp: $!\n";
        rename "$state/info.json.tmp", "$state/info.json" or die "cannot replace $state/info.json: $!\n";
    } else {
        print STDERR "pg-backup-metrics: repository unreadable: $why; exporting the last readable backup times\n";
        ($doc) = stanzas(slurp("$state/info.json"));
        $doc //= [];
    }
    my (%stop, %took);
    for my $s (@$doc) {
        for my $b (@{ $s->{backup} || [] }) {
            my $t = $b->{timestamp}{stop} // next;
            my $k = join "\0", $s->{name} // '', $b->{database}{'repo-key'} // '', $b->{type} // '';
            next if exists $stop{$k} && $stop{$k} >= $t;
            $stop{$k} = $t;
            $took{$k} = $t - ($b->{timestamp}{start} // $t);
        }
    }
    my @m;
    push @m, "# HELP pipeshub_pg_backup_repository_up 1 when the last pgbackrest info read every stanza as ok or as having no backup yet.",
        "# TYPE pipeshub_pg_backup_repository_up gauge", "pipeshub_pg_backup_repository_up $up";
    push @m, "# HELP pipeshub_pg_backup_last_success_timestamp_seconds Stop time of the newest backup of each type in each repository.",
        "# TYPE pipeshub_pg_backup_last_success_timestamp_seconds gauge";
    my @keys = sort keys %stop;
    for my $k (@keys) {
        my ($st, $repo, $type) = split /\0/, $k, 3;
        push @m, sprintf('pipeshub_pg_backup_last_success_timestamp_seconds{stanza="%s",repo="%s",type="%s"} %d', esc($st), esc($repo), esc($type), $stop{$k});
    }
    push @m, "# HELP pipeshub_pg_backup_last_duration_seconds How long the newest backup of each type in each repository ran.",
        "# TYPE pipeshub_pg_backup_last_duration_seconds gauge";
    for my $k (@keys) {
        my ($st, $repo, $type) = split /\0/, $k, 3;
        push @m, sprintf('pipeshub_pg_backup_last_duration_seconds{stanza="%s",repo="%s",type="%s"} %d', esc($st), esc($repo), esc($type), $took{$k});
    }
    push @m, "# HELP pipeshub_pg_backup_schedule_seconds The scheduler's interval: a full backup every full, any backup every incr.",
        "# TYPE pipeshub_pg_backup_schedule_seconds gauge",
        "pipeshub_pg_backup_schedule_seconds{type=\"full\"} $full_s", "pipeshub_pg_backup_schedule_seconds{type=\"incr\"} $incr_s";
    # written once by pg-backup.sh when the scheduler first starts (kept across restarts): how long it has run without a full backup
    if (my ($since) = slurp("$state/started") =~ /^(\d+)/) {
        push @m, "# HELP pipeshub_pg_backup_scheduler_start_timestamp_seconds When the backup scheduler first started on this repository.",
            "# TYPE pipeshub_pg_backup_scheduler_start_timestamp_seconds gauge", "pipeshub_pg_backup_scheduler_start_timestamp_seconds $since";
    }
    # "<epoch> <1|0>" of the last backup attempt, written by pg-backup.sh; absent until the first attempt
    if (my ($at, $ok) = slurp("$state/attempt") =~ /^(\d+) ([01])\b/) {
        push @m, "# HELP pipeshub_pg_backup_last_attempt_timestamp_seconds When the last backup attempt ended.",
            "# TYPE pipeshub_pg_backup_last_attempt_timestamp_seconds gauge", "pipeshub_pg_backup_last_attempt_timestamp_seconds $at",
            "# HELP pipeshub_pg_backup_last_attempt_success 1 when the last backup attempt succeeded.",
            "# TYPE pipeshub_pg_backup_last_attempt_success gauge", "pipeshub_pg_backup_last_attempt_success $ok";
    }
    my $drops = gap_segments($gaps);
    push @m, "# HELP pipeshub_pg_backup_wal_gap_segments WAL segments the server dropped that no full backup covers yet.",
        "# TYPE pipeshub_pg_backup_wal_gap_segments gauge", "pipeshub_pg_backup_wal_gap_segments $drops";
    open(my $h, '>', "$out.tmp") or die "cannot write $out.tmp: $!\n";
    print $h join("\n", @m), "\n"; close $h or die "cannot write $out.tmp: $!\n";
    rename "$out.tmp", $out or die "cannot replace $out: $!\n";
}

sub serve {
    my ($port, $file, $gaps) = @_;
    die "serve: three arguments\n" unless defined $gaps;
    require IO::Socket::INET;
    my $srv = IO::Socket::INET->new(LocalPort => $port, Listen => 16, ReuseAddr => 1, Proto => 'tcp')
        or die "pg-backup-metrics: cannot listen on $port: $!\n";
    $SIG{PIPE} = 'IGNORE';
    while (1) {
        my $c = $srv->accept or next;
        # a client that sends no request within 5 s is dropped, so it cannot hold the only worker
        eval {
            local $SIG{ALRM} = sub { die "timeout\n" };
            alarm 5;
            while (defined(my $l = <$c>)) { last if $l =~ /^\r?\n$/ }
            alarm 0;
            my $body = slurp($file);
            if (length $body) { my $n = gap_segments($gaps); $body =~ s/^pipeshub_pg_backup_wal_gap_segments \S+$/pipeshub_pg_backup_wal_gap_segments $n/m }
            my $status = length $body ? '200 OK' : '503 Service Unavailable';
            print $c "HTTP/1.1 $status\r\nContent-Type: text/plain; version=0.0.4\r\nContent-Length: ", length($body),
                "\r\nConnection: close\r\n\r\n", $body;
        };
        alarm 0;
        close $c;
    }
}
