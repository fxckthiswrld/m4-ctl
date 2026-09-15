import asyncio
import sys
import threading
import unittest


@unittest.skipUnless(sys.platform == "darwin", "requires macOS Cocoa run loop")
class MacNativeRunLoopTests(unittest.IsolatedAsyncioTestCase):
    async def test_cocoa_timer_runs_on_main_thread_while_asyncio_waits(self):
        from Foundation import NSTimer
        from gaia_transport import _mac_run_loop

        delivered = asyncio.Event()
        callback_threads = []

        def callback(timer):
            callback_threads.append(threading.current_thread())
            delivered.set()

        timer = NSTimer.scheduledTimerWithTimeInterval_repeats_block_(0.02, False, callback)
        pump = asyncio.create_task(_mac_run_loop())
        try:
            await asyncio.wait_for(delivered.wait(), 2.0)
            self.assertEqual(callback_threads, [threading.main_thread()])
        finally:
            timer.invalidate()
            pump.cancel()
            await asyncio.gather(pump, return_exceptions=True)

    async def test_registered_delegate_has_native_callback_signatures(self):
        from gaia_transport import MacSppTransport

        first = MacSppTransport("00:11:22:33:44:55")._make_delegate()
        second = MacSppTransport("00:11:22:33:44:55")._make_delegate()
        self.assertIs(type(first), type(second))
        self.assertIsNot(first, second)
        signature = first.methodSignatureForSelector_(b"rfcommChannelOpenComplete:status:")
        self.assertEqual(signature.methodReturnType(), b"v")
        self.assertEqual(signature.getArgumentTypeAtIndex_(3), b"i")
        data = first.rfcommChannelData_data_length_.__metadata__()["arguments"]
        self.assertEqual(data[3]["type"], b"n^v")
        self.assertEqual(data[3]["c_array_length_in_arg"], 4)


if __name__ == "__main__":
    unittest.main()
