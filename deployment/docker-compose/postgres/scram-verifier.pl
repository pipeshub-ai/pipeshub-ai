#!/usr/bin/perl
# Prints the SCRAM-SHA-256 verifier PostgreSQL stores for the password in the environment variable named by $ARGV[0]
# (RFC 5802/7677, 4096 iterations). The password is read from the environment, never from argv, and only the verifier leaves this
# script, so the server never sees the clear text. Printable ASCII only: that makes SASLprep a no-op, so the server accepts the same password.
use strict;
use warnings;
use Digest::SHA qw(sha256 hmac_sha256);
use MIME::Base64 qw(encode_base64);

my $name = $ARGV[0] // die "usage: scram-verifier.pl VARIABLE\n";
my $pw = $ENV{$name};
die "$name is empty\n" unless defined $pw && length $pw;
die "$name must be printable ASCII (letters, digits, punctuation)\n" unless $pw =~ /\A[\x20-\x7e]+\z/;

open(my $rnd, '<:raw', '/dev/urandom') or die "no /dev/urandom: $!\n";
read($rnd, my $salt, 16) == 16 or die "short read from /dev/urandom\n";
my $iter = 4096;
my $u = hmac_sha256($salt . "\0\0\0\1", $pw);
my $salted = $u;
for (2 .. $iter) { $u = hmac_sha256($u, $pw); $salted ^= $u; }
my $stored = sha256(hmac_sha256('Client Key', $salted));
my $server = hmac_sha256('Server Key', $salted);
my ($s, $st, $sv) = map { my $e = encode_base64($_, ''); $e } ($salt, $stored, $server);
print "SCRAM-SHA-256\$$iter:$s\$$st:$sv\n";
