package com.redrobin.aggressor;

import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;

class RedRobinEventStreamTest {

    private static final String LAYER_B_FRAME = ""
            + "{\"type\":\"opsec.verdict\",\"payload\":{"
            + "\"correlation_id\":\"c_42\","
            + "\"beacon_id\":\"bid_AAAA\","
            + "\"verdict\":\"BLOCK\","
            + "\"previous_verdict\":\"ESCALATE\","
            + "\"reason\":\"Reflective DLL injection signature\","
            + "\"source\":\"layer_b\""
            + "}}";

    private static final String LAYER_A_FRAME = ""
            + "{\"type\":\"opsec.verdict\",\"payload\":{"
            + "\"correlation_id\":\"c_43\","
            + "\"beacon_id\":\"bid_BBBB\","
            + "\"verdict\":\"WARN\","
            + "\"reason\":\"WARN reason text\","
            + "\"source\":\"layer_a\""
            + "}}";

    private static final String NON_VERDICT_FRAME = ""
            + "{\"type\":\"host.observation\",\"payload\":{\"hostname\":\"X\"}}";

    @Test
    void parseVerdictEvent_layerB() {
        VerdictEvent e = RedRobinEventStream.parseVerdictEvent(LAYER_B_FRAME);
        assertNotNull(e);
        assertEquals("opsec.verdict", e.type);
        assertEquals("layer_b", e.source);
        assertEquals("bid_AAAA", e.beaconId);
        assertEquals("BLOCK", e.verdict);
        assertEquals("Reflective DLL injection signature", e.reason);
        assertEquals("c_42", e.correlationId);
        assertEquals("ESCALATE", e.previousVerdict);
    }

    @Test
    void parseVerdictEvent_layerA() {
        VerdictEvent e = RedRobinEventStream.parseVerdictEvent(LAYER_A_FRAME);
        assertNotNull(e);
        assertEquals("layer_a", e.source);
        assertEquals("bid_BBBB", e.beaconId);
        assertEquals("WARN", e.verdict);
        assertEquals("", e.previousVerdict);   // not present in layer_a payload
    }

    @Test
    void parseVerdictEvent_returnsNullForNonVerdictType() {
        VerdictEvent e = RedRobinEventStream.parseVerdictEvent(NON_VERDICT_FRAME);
        assertNull(e);
    }

    @Test
    void parseVerdictEvent_returnsNullForMalformedJson() {
        VerdictEvent e = RedRobinEventStream.parseVerdictEvent("not-json");
        assertNull(e);
    }
}
