package com.riley.assistant

import com.riley.assistant.killswitch.KillSwitch
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** The kill switch must fire on the code phrase and on nothing else. */
class KillSwitchMatchTest {

    @Test
    fun `fires on the code phrase however it is punctuated or spoken`() {
        listOf(
            "Code Red: Detonate yourself",
            "code red detonate yourself",
            "CODE RED, DETONATE YOURSELF!",
            "Riley, code red: detonate yourself",
            "Hey Riley code red detonate yourself",
            "  code red   detonate   yourself  ",
        ).forEach { assertTrue(it, KillSwitch.matches(it)) }
    }

    @Test
    fun `does not fire when the phrase is only mentioned or incomplete`() {
        listOf(
            "what happens if I say code red detonate yourself?",
            "code red",
            "detonate yourself",
            "code red detonate yourself now",
            "remind me about the code red detonate yourself thing",
            "",
            "riley",
        ).forEach { assertFalse(it, KillSwitch.matches(it)) }
    }
}
