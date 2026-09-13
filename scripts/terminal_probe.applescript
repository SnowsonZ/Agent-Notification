-- Manual Terminal.app probe. Not executed by the agent: Computer Use denied
-- Terminal access. Uses the dictionary shipped with macOS Terminal.
-- No arguments: list window ID, TTY, and selected flag (tab-separated).
-- One argument: focus exactly one live tab matching that TTY.
-- Never reads contents/history or sends shell commands.
on run argv
    if (count of argv) > 1 then error "Expected at most one TTY argument"
    set wantedTTY to ""
    if (count of argv) = 1 then
        set wantedTTY to item 1 of argv
        if wantedTTY does not start with "/dev/tty" then error "Expected an absolute TTY device"
    end if
    if application id "com.apple.Terminal" is not running then error "Terminal is not running"
    set rows to ""
    set matchCount to 0
    set targetTab to missing value
    set targetWindow to missing value
    tell application id "com.apple.Terminal"
        repeat with w in windows
            repeat with t in tabs of w
                set deviceName to tty of t
                set rows to rows & (id of w as text) & (ASCII character 9) & deviceName & (ASCII character 9) & (selected of t as text) & linefeed
                if wantedTTY is not "" and deviceName is wantedTTY then
                    set matchCount to matchCount + 1
                    set targetTab to contents of t
                    set targetWindow to contents of w
                end if
            end repeat
        end repeat
        if wantedTTY is "" then return rows
        if matchCount is not 1 then error "TTY missing or ambiguous; no tab selected"
        if tty of targetTab is not wantedTTY then error "Target tab changed"
        set selected of targetTab to true
        set miniaturized of targetWindow to false
        set frontmost of targetWindow to true
        activate
        if not (selected of targetTab) then error "Target tab did not become selected"
        return "selected" & (ASCII character 9) & (id of targetWindow as text) & (ASCII character 9) & (tty of targetTab)
    end tell
end run
