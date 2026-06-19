package com.instaoff.app

import android.content.Intent
import android.graphics.Color
import android.os.Bundle
import android.view.animation.DecelerateInterpolator
import android.widget.ImageView
import android.widget.RelativeLayout
import androidx.appcompat.app.AppCompatActivity

class SplashActivity : AppCompatActivity() {

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        // Dark premium background color
        val darkBackgroundColor = Color.parseColor("#08090D") 

        // Create main splash container
        val rootLayout = RelativeLayout(this).apply {
            setBackgroundColor(darkBackgroundColor)
            layoutParams = RelativeLayout.LayoutParams(
                RelativeLayout.LayoutParams.MATCH_PARENT,
                RelativeLayout.LayoutParams.MATCH_PARENT
            )
        }

        // PTM App Logo Icon
        val logoSize = (200 * resources.displayMetrics.density).toInt()
        val logoView = ImageView(this).apply {
            setImageResource(R.drawable.app_logo)
            scaleType = ImageView.ScaleType.FIT_CENTER
            layoutParams = RelativeLayout.LayoutParams(logoSize, logoSize).apply {
                addRule(RelativeLayout.CENTER_IN_PARENT)
            }
            
            // Set initial state for animations
            alpha = 0f
            scaleX = 0.8f
            scaleY = 0.8f
        }

        rootLayout.addView(logoView)
        setContentView(rootLayout)

        // Fade-in and zoom-in entrance micro-animations
        logoView.animate()
            .alpha(1f)
            .scaleX(1f)
            .scaleY(1f)
            .setDuration(1200)
            .setInterpolator(DecelerateInterpolator())
            .withEndAction {
                // Hold splash for 1 second, then launch MainActivity
                logoView.postDelayed({
                    val intent = Intent(this@SplashActivity, MainActivity::class.java)
                    startActivity(intent)
                    finish()
                    // Apply smooth cross-fade transition between Splash and Dashboard
                    overridePendingTransition(android.R.anim.fade_in, android.R.anim.fade_out)
                }, 1000)
            }
            .start()
    }
}
