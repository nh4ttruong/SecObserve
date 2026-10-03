export function getServerTimeZone(): string {
    try {
        const time_zone = JSON.parse(localStorage.getItem("settings") ?? "{}").time_zone;
        if (time_zone) {
            // Throws a RangeError if the browser does not know the time zone
            new Intl.DateTimeFormat(undefined, { timeZone: time_zone });
            return time_zone;
        }
    } catch {
        // fall back to the time zone of the browser
    }
    return Intl.DateTimeFormat().resolvedOptions().timeZone;
}

/** Current offset of the server time zone, e.g. "UTC+07:00" */
export function getServerTimeZoneOffset(): string {
    const offset =
        new Intl.DateTimeFormat("en-US", { timeZone: getServerTimeZone(), timeZoneName: "longOffset" })
            .formatToParts(new Date())
            .find((part) => part.type === "timeZoneName")?.value ?? "GMT";
    return offset === "GMT" ? "UTC+00:00" : offset.replace("GMT", "UTC");
}

// Milliseconds the server time zone is ahead of UTC at the given instant
function getServerOffset(instant: number): number {
    const parts = new Intl.DateTimeFormat("en-US", {
        timeZone: getServerTimeZone(),
        hourCycle: "h23",
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
        hour: "2-digit",
        minute: "2-digit",
        second: "2-digit",
    }).formatToParts(new Date(instant));
    const part = (type: string) => Number(parts.find((p) => p.type === type)?.value);
    const wallTime = Date.UTC(
        part("year"),
        part("month") - 1,
        part("day"),
        part("hour"),
        part("minute"),
        part("second")
    );
    return wallTime - Math.floor(instant / 1000) * 1000;
}

/** Instant at which a calendar day, e.g. "2026-10-03", starts in the server time zone */
export function getServerDayStart(day: string, addDays = 0): Date {
    const [year, month, date] = day.split("-").map(Number);
    const midnight = Date.UTC(year, month - 1, date + addDays);
    // The second lookup corrects the guess when a DST change lies between it and the result
    return new Date(midnight - getServerOffset(midnight - getServerOffset(midnight)));
}

/** Calendar date in the server time zone, e.g. "2026-10-03" */
export function getServerDate(date: Date): string {
    const parts = new Intl.DateTimeFormat("en-US", {
        timeZone: getServerTimeZone(),
        year: "numeric",
        month: "2-digit",
        day: "2-digit",
    }).formatToParts(date);
    const part = (type: string) => parts.find((p) => p.type === type)?.value;
    return `${part("year")}-${part("month")}-${part("day")}`;
}
