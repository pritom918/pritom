package com.instaoff.app

import android.content.Intent
import android.graphics.Color
import android.net.Uri
import android.os.Bundle
import android.widget.RelativeLayout
import android.widget.VideoView
import androidx.appcompat.app.AppCompatActivity

class SplashActivity : AppCompatActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        // Seamless black background matching the video splash
        val rootLayout = RelativeLayout(this).apply {
            setBackgroundColor(Color.BLACK)
            layoutParams = RelativeLayout.LayoutParams(
                RelativeLayout.LayoutParams.MATCH_PARENT,
                RelativeLayout.LayoutParams.MATCH_PARENT
            )
        }

        val videoView = VideoView(this).apply {
            val params = RelativeLayout.LayoutParams(
                RelativeLayout.LayoutParams.MATCH_PARENT,
                RelativeLayout.LayoutParams.MATCH_PARENT
            ).apply {
                addRule(RelativeLayout.CENTER_IN_PARENT)
            }
            layoutParams = params
        }

        rootLayout.addView(videoView)
        setContentView(rootLayout)

        try {
            // Configure path to resource raw splash_video.mp4
            val videoUri = Uri.parse("android.resource://$packageName/${R.raw.splash_video}")
            videoView.setVideoURI(videoUri)

            videoView.setOnPreparedListener { mp ->
                // Adjust screen display scaling and play
                mp.isLooping = false
                videoView.start()
            }

            // Move to MainActivity once video finishes playing
            videoView.setOnCompletionListener {
                navigateToHome()
            }

            // Error fallback: If video player encounters an issue, skip to Dashboard instantly
            videoView.setOnErrorListener { _, _, _ ->
                navigateToHome()
                true
            }
        } catch (e: Exception) {
            navigateToHome()
        }
    }

    private fun navigateToHome() {
        val intent = Intent(this@SplashActivity, MainActivity::class.java)
        startActivity(intent)
        finish()
        // Smooth cross-fade transition
        overridePendingTransition(android.R.anim.fade_in, android.R.anim.fade_out)
    }
}
