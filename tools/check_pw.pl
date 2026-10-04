#!/usr/bin/perl
# =============================================================================
#  用系统 libcrypt 校验密码（支持 yescrypt）
#
#  为什么这个可靠：
#    Perl 的 crypt() 直接调用系统 libcrypt，而 Ubuntu 24.04 的 libxcrypt
#    完整支持 yescrypt（$y$）。用 shadow 里的原始哈希当 salt 重算，
#    结果相同即说明密码正确。不依赖 python 的 crypt 模块（它不可靠），
#    也不需要分配 pty 去跑 su。
#
#  用法: sudo perl check_pw.pl <用户名> <候选密码> [更多候选...]
# =============================================================================
use strict;
use warnings;

my $user = shift @ARGV or die "用法: check_pw.pl <用户名> <密码...>\n";
my @cands = @ARGV;
push @cands, '123456' unless @cands;

my $line = `getent shadow $user`;
chomp $line;
die "读不到 $user 的 shadow 记录（需要 sudo）\n" unless $line;

my @f = split /:/, $line;
my $hash = $f[1];
die "shadow 里没有密码哈希\n" unless $hash;

# 算法前缀 -> 名称
my $algo = substr($hash, 0, 3);
my %names = ('$y$' => 'yescrypt', '$6$' => 'sha512crypt', '$5$' => 'sha256crypt', '$1$' => 'md5crypt');
printf "用户: %s\n", $user;
printf "算法: %s (%s)\n", $algo, ($names{$algo} // '未知');
printf "哈希: %s...\n\n", substr($hash, 0, 28);

my $any = 0;
for my $pw (@cands) {
    my $calc = crypt($pw, $hash);
    my $ok = (defined $calc && $calc eq $hash);
    $any = 1 if $ok;
    printf "  %-12s -> %s\n", $pw, $ok ? "MATCH" : "NO";
}
print "\n结论: ", ($any ? "至少有一个候选密码正确" : "所有候选都不匹配"), "\n";
