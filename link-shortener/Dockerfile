FROM golang:1.26-alpine AS build
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/snip ./cmd/snip \
 && CGO_ENABLED=0 go build -trimpath -ldflags="-s -w" -o /out/loadgen ./cmd/loadgen

# Small image (~48 MB, mostly the two static binaries) on distroless, no shell, runs as an unprivileged user.
FROM gcr.io/distroless/static-debian12:nonroot
COPY --from=build /out/ /usr/local/bin/
EXPOSE 8080
HEALTHCHECK --interval=10s --timeout=3s CMD ["/usr/local/bin/snip", "healthcheck"]
ENTRYPOINT ["/usr/local/bin/snip"]
CMD ["serve"]
