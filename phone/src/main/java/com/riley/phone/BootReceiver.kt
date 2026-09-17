package com.riley.phone

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent

/** Riley has to be reachable again after a restart. */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        when (intent.action) {
            Intent.ACTION_BOOT_COMPLETED, Intent.ACTION_MY_PACKAGE_REPLACED -> RingService.start(context)
        }
    }
}
