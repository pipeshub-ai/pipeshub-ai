/*
 * pxh-relay PORT: the sandbox broker's `connect(port)` tunnel, for images without python3 (P5.1-f).
 *
 * Protocol (the same as the broker's Python relay): connect to 127.0.0.1:PORT; on failure exit 111
 * without writing anything; on success write one ack byte (0x01) to stdout, then copy stdin to the
 * socket and the socket to stdout. End of stdin half-closes the socket (shutdown SHUT_WR); end of the
 * socket ends the relay. Static, no allocation, no name resolution.
 */
#include <arpa/inet.h>
#include <errno.h>
#include <netinet/in.h>
#include <poll.h>
#include <signal.h>
#include <stdlib.h>
#include <sys/socket.h>
#include <unistd.h>

#define BUFFER 65536

static int write_all(int fd, const char *data, ssize_t length) {
    while (length > 0) {
        ssize_t written = write(fd, data, (size_t)length);
        if (written < 0) {
            if (errno == EINTR) continue;
            return -1;
        }
        data += written;
        length -= written;
    }
    return 0;
}

int main(int argc, char **argv) {
    static char buffer[BUFFER];
    if (argc != 2) return 2;
    char *end = NULL;
    long port = strtol(argv[1], &end, 10);
    if (end == argv[1] || *end != '\0' || port <= 0 || port > 65535) return 2;
    signal(SIGPIPE, SIG_IGN);

    int sock = socket(AF_INET, SOCK_STREAM, 0);
    if (sock < 0) return 111;
    struct sockaddr_in address = {0};
    address.sin_family = AF_INET;
    address.sin_port = htons((unsigned short)port);
    address.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    if (connect(sock, (struct sockaddr *)&address, sizeof address) != 0) return 111;
    if (write_all(1, "\x01", 1) != 0) return 1;

    struct pollfd fds[2] = {{.fd = 0, .events = POLLIN}, {.fd = sock, .events = POLLIN}};
    for (;;) {
        if (poll(fds, 2, -1) < 0) {
            if (errno == EINTR) continue;
            return 1;
        }
        if (fds[0].revents & (POLLIN | POLLHUP | POLLERR)) {
            ssize_t got = read(0, buffer, sizeof buffer);
            if (got > 0) {
                if (write_all(sock, buffer, got) != 0) return 1;
            } else if (got == 0 || errno != EINTR) {
                shutdown(sock, SHUT_WR);
                fds[0].fd = -1;
            }
        }
        if (fds[1].revents & (POLLIN | POLLHUP | POLLERR)) {
            ssize_t got = read(sock, buffer, sizeof buffer);
            if (got > 0) {
                if (write_all(1, buffer, got) != 0) return 1;
            } else if (got == 0) {
                return 0;
            } else if (errno != EINTR) {
                return 1;
            }
        }
    }
}
