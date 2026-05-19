package com.redrobin.aggressor;

/**
 * Public-final-fields DTO emitted by RedRobinEventStream.pollEvent().
 *
 * Sleep reflects on this class via the [$obj fieldName] bracket syntax
 * (verified in Task #115 via RedRobinHttp.Response — same pattern).
 * All fields are public final so Sleep's getField fallback resolves
 * without setAccessible.
 */
public class VerdictEvent {
    public final String type;             // "opsec.verdict"
    public final String source;           // "layer_a" or "layer_b"
    public final String beaconId;
    public final String verdict;          // "BLOCK", "WARN", "ALLOW", "ESCALATE"
    public final String reason;
    public final String correlationId;
    public final String previousVerdict;  // populated for layer_b only; "" otherwise

    public VerdictEvent(String type, String source, String beaconId,
                        String verdict, String reason,
                        String correlationId, String previousVerdict) {
        this.type = type;
        this.source = source;
        this.beaconId = beaconId;
        this.verdict = verdict;
        this.reason = reason;
        this.correlationId = correlationId;
        this.previousVerdict = previousVerdict;
    }
}
